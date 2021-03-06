# CSPro .DCF dictionary specification parser
# For use with DHS hierarchical data
# Harry Gibson 2015-2016
import re
import os
from difflib import SequenceMatcher as SM
from collections import defaultdict
def parseDCF(dcfFile, fileCode=None, expandRanges = "All"):
    '''
    Parse a .DCF file (CSPro dictionary specification) into a structured object.

    Developed for use in particular with DCF files provided in the "hierarchical data"
    from DHS, but may be more generally applicable to CSPro format files.
    Produces table and value specification files that can be used to parse and then
    interpret the values from the corresponding .DAT data files.

    The result is a 2-tuple.
    The first item of this is a list where each item is a dictionary that represents
    a "DHS Recode", i.e. the specification for one column in a given table.
    This dictionary is suitable for writing out to a CSV file.
    The item dictionary may have a "Values" key, whose value is itself a list of
    dictionaries. Each of these subdictionaries represents a value that the associated
    column can have (its value domain). The contents of the values key, if present, should be
    written out to a separate CSV file.

    The second item in the result tuple is a similar list of dictionaries that represent the
    "relationships" defined in the file, i.e. the documented table joins that can be created.
    Note, however, that this doesn't specify everything that's possible: for example the REC21
    child table can generally be joined to a record in the RECH1 household schedule table based
    on REC21.CASEID = RECH1.HHID and REC21.B16 = RECH1.HVIDX, but this is never actually specified!

    Usage example:
        schemafields = ['ItemType', 'FileCode', 'RecordName', 'RecordTypeValue',
                    'RecordLabel', 'Name', 'Label', 'Start', 'Len',
                    'Occurrences', 'ZeroFill', 'DecimalChar', 'Decimal']
        valfields = ['FileCode', 'Name', 'Value', 'ValueDesc', 'ValueType']
        relfields = ['FileCode', 'RelName', 'PrimaryTable', 'PrimaryLink',
                    'SecondaryTable', 'SecondaryLink']
        parsedItems, parsedRelations = parseDCF (dcfFileName)
        for item in parsedItems:
            # outWriter is a csv writer you've set up with fieldnames as above
            outWriter.writerow([item[k]] if item.has_key[k] else '' for k in schemafields)
            if 'Values' in item and len(item['Values'] > 0):
                vals = item['Values']
                for val in vals:
                    valWriter.writerow([item['FileCode'], item['Name'],
                                        val[0], val[1], val[2]])
        for item in parsedRelations:
            relWriter.writeRow([item[k] if item.has_key(k) else '' for k in relfieldnames])
    '''
    # We simply read the .DCF format file in line order, imputing the hierarchical structure from the order
    # of things, i.e. the level and record of an item is the previous found level or record
    # We also take advantage of the blank line between chunks as a line delimiter.
    global parsedLines
    parsedLines = 0

    # if true then any valuesets where there are multiple ranges given will be expanded out to define each
    # value explicitly. This risks millions of rows in the output if the DCF specifies e.g.
    # 0:12 Number of months
    # 20 Bananas
    # 100:9999998
    # 9999999 I'm Stupid
    # Values can be "All", "Multiple", "None"
    RANGE_EXPANSION_STRATEGY = expandRanges #"All"
    # Add a safety flag so no one range gets expanded to more than this number of rows
    RANGE_EXPANSION_LIMIT = 10000

    # within-survey "globals" i.e. things we need to keep track of between items
    currentRecordName = 'N/A'
    currentRecordLabel = 'N/A'
    currentRecordType = 'N/A'

    myRelationProcessor = RelationRowProcessor()

    currentLevelName = ''
    currentLevelLabel = ''
    currentSurveyDecChar = False
    currentSurveyZeroFill = False

    currentValues = []
    skippingChunk = False

    currentlyParsing = "None"
    currentIds = []

    myRecords = {}
    myLevels = {}
    myItems = []
    myRelations = []
    myCountries = {}
    mySkippedChunks = []

    # Get a unique reference code for this dcf file that should be entered into the output data.
    # Would be cleaner / more general to accept this as a method parameter.
    currentSurveyCode = fileCode or os.path.extsep.join(os.path.basename(dcfFile).split(os.path.extsep)[:-1])
    chunkInfo = {
        'FileCode':currentSurveyCode
    }

    with open(dcfFile) as fileIn:
        # We read through the dcf line-by-line. The structure of the file is given by the order in
        # which sections occur, and the sections ("chunks") are delimited by blank lines. We take
        # advantage of these facts to build the output record specification and value specification
        # tables.
        for line in fileIn:
            parsedLines +=1
            # Are we on a chunk start (a line with something in [Brackets])?
            # If so reset the chunkInfo global, and anything else as appropriate
            if line.find('[Level]') != -1:
                currentChunkType = "Level"
                skippingChunk = False
                chunkInfo = {}
            elif line.find('[Record]') != -1:
                currentChunkType = "Record"
                currentlyParsing = "Records"
                skippingChunk = False
                chunkInfo = {}
            elif line.find('[Item]') != -1:
                currentChunkType = "Item"
                skippingChunk = False
                chunkInfo = {}
            elif line.find('[ValueSet]') != -1:
                currentChunkType = "ValueSet"
                skippingChunk = False
                chunkInfo = {}
            elif line.find('[IdItems]') != -1:
                # Reset the iditems global as well
                currentChunkType = "IdItems"
                skippingChunk = False
                currentlyParsing = "IdItems"
                currentIds = []
                chunkInfo = {}
            elif line.find('[Dictionary]') != -1:
                currentChunkType = "Dictionary"
                currentlyParsing = "Dictionary"
                skippingChunk = False
                recordTypeStart = 0
                recordTypeLen = 0
            elif line.find('[Relation]') != -1:
                currentChunkType = "Relation"
                currentlyParsing = "Relation"
                skippingChunk = False
                chunkInfo = {}

            elif line.find('[') == 0 and line.find(']') != -1:
                # This is some chunk we don't know and/or care about.
                skippingChunk = True
                mySkippedChunks.append(line)

            # Or are we on the end of a chunk, marked by a blank line? This is the point at which
            # we may want to do something with the previous lines of info
            elif line == '\n':
                if skippingChunk:
                    # this was a bunch of lines we skip (e.g. those following '[Dictionary]')
                    skippingChunk = False
                else:
                    if currentChunkType == 'Dictionary':
                        # This will be the first item in the file and will be written to the first
                        # row of the output. It's an item that describes for all lines of the data file
                        # where the record type can be found (start/len). It's a fudge to make this fit
                        # the overall row format which is designed to describe an item
                        chunkInfo['RecordName'] = '*'
                        chunkInfo['RecordLabel'] = '*'
                        chunkInfo['RecordTypeValue'] = '*'
                        # the record type positioning is labelled in the DCF as "RecordTypeStart"(Len)
                        # but we want to record it into the standard Start/Len columns of the output CSV
                        # so just copy it over
                        chunkInfo['Start'] = chunkInfo['RecordTypeStart']
                        chunkInfo['Len'] = chunkInfo['RecordTypeLen']
                        chunkInfo['ItemType'] = 'RecordDesciption'
                        myItems.append(chunkInfo)
                        # set the default values for the item parsing info
                        currentSurveyZeroFill = chunkInfo['ZeroFill']
                        currentSurveyDecChar = chunkInfo['DecimalChar']

                    # If we're at the end of a chunk defining a level or record then place the
                    # info into the globals so that the item parser will read them for items
                    # that FOLLOW afterward
                    elif currentChunkType == 'Level':
                        currentLevelName = chunkInfo['Name']
                        currentLevelLabel = chunkInfo['Label']
                        if myLevels.has_key(currentLevelName):
                            if myLevels[currentLevelName] == currentLevelLabel:
                                print "Warning, duplicate level name/label encountered at line "+str(parsedLines)
                            else:
                                raise ValueError("Duplicate level name encountered at line {0!s} with non-matched label".
                                                 format(parsedLines))
                        myLevels[currentLevelName] = currentLevelLabel

                    elif currentChunkType == 'Record':
                        # save into dirty globals so the subsequent items know what record they belong to
                        currentRecordName = chunkInfo['Name']
                        currentRecordLabel = chunkInfo['Label']
                        currentRecordType = chunkInfo['RecordTypeValue']

                        # At the end of a record chunk, we save an "item" with the new record name/type/label
                        # reflecting the id item for this record. In other words the first output row of each record
                        # will describe the record itself and in particular the start/len of the id item(s)
                        chunkInfo['FileCode']=currentSurveyCode
                        # apply the parent hierarchical labels, just stored in simple globals
                        chunkInfo['RecordName'] = currentRecordName
                        chunkInfo['RecordLabel'] = currentRecordLabel
                        chunkInfo['RecordTypeValue'] = currentRecordType.strip("'")
                        chunkInfo['LevelName'] = currentLevelName
                        chunkInfo['LevelLabel'] = currentLevelLabel
                        chunkInfo['ItemType'] = 'IdItem'
                        for iditem in currentIds:
                            # add a row for each id item
                            # Normally there will only be one (which may implicitly code more than one within it e.g.
                            # caseid includes HHID and another number), but sometimes (looking at you, HIV datasets)
                            # there may be several encoded as several items
                            newItem = {}
                            for i in chunkInfo:
                                # copy the common record-related stuff, careful not to just modify chunkInfo and add it repeatedly
                                # as that wouldn't work (reference types and all that jazz)
                                newItem[i] = chunkInfo[i]
                            newItem['Name'] = iditem['Name']
                            newItem['Label'] = iditem['Label']
                            newItem['Start'] = iditem['Start']
                            newItem['Len'] = iditem['Len']
                            myItems.append(newItem)

                        if myRecords.has_key(currentRecordName):
                            if myRecords[currentRecordName] == currentRecordLabel:
                                print "Warning, duplicate record name/label encountered at line "+str(parsedLines)
                            else:
                                raise ValueError("Duplicate record name encountered at line {0!s} with non-matched label".
                                                 format(parsedLines))
                        myRecords[currentRecordName] = currentRecordLabel

                    #elif currentChunkType == 'Dictionary'

                    # If we're at the end of a chunk defining a valueset then place the info
                    # into the last-processed item - valueset comes AFTER the item and a blank line,
                    # so the item will have already been added to the output list myItems
                    elif currentChunkType == 'ValueSet':
                        # check it matches-ish. Either starts the same or text similarity is high
                        # - sometimes they abbreviate the valueset but not the previous label
                        s1 = chunkInfo['Label']
                        s2 = myItems[-1]['Label']

                        simRatio = SM(None, s1, s2).ratio()
                        if not (simRatio > 0.7 or chunkInfo['Label'].find(myItems[-1]['Label']) == 0):
                            print ("Warning, valueset did not seem to match item at line {0!s} of file {1!s} - please check!".
                                   format(parsedLines,dcfFile))

                        if 'ValueRanges' in chunkInfo:
                            # We can optionally expand each value range out to the individual values.
                            # We are more likely to want to do this if there are multiple value ranges as this
                            # tends to imply different meanings for different ranges of values, e.g.
                            # 1:12=age in months, 13:112 = (age in years +12)
                            # In either case we probably don't want to expand any huge ranges e.g. 10:9999998, as these
                            # would normally be a gap between real values 0-10 and a missing value 9999999.
                            gotMultipleRanges = True if len(chunkInfo['ValueRanges']) > 1 else False
                            for rangeInfo in chunkInfo['ValueRanges']:
                                thisRangeMin = int(rangeInfo[0])
                                thisRangeMax = int(rangeInfo[1])
                                thisRangeDesc = rangeInfo[2]
                                rangeSize = (thisRangeMax - thisRangeMin) + 1
                                # break if something's wrong with the min / max intepretation
                                if rangeSize <= 1:
                                    raise ValueError("Error parsing range in file "+dcfFile+" at line "+str(parsedLines))
                                if rangeSize <= RANGE_EXPANSION_LIMIT:
                                    if gotMultipleRanges:
                                        if (RANGE_EXPANSION_STRATEGY in ["All", "Multiple"]):
                                            for expandedVal in range(thisRangeMin, thisRangeMax+1):
                                                currentValues.append((expandedVal, thisRangeDesc, "ExpandedRange"))
                                        else:
                                            currentValues.append((thisRangeMin, thisRangeDesc, "MultiRangeMin"))
                                            currentValues.append((thisRangeMax, thisRangeDesc, "MultiRangeMax"))
                                    else:
                                        if RANGE_EXPANSION_STRATEGY == "All":
                                            for expandedVal in range(thisRangeMin, thisRangeMax+1):
                                                currentValues.append((expandedVal, thisRangeDesc, "ExpandedRange"))
                                        else:
                                            currentValues.append((thisRangeMin, thisRangeDesc, "RangeMin"))
                                            currentValues.append((thisRangeMax, thisRangeDesc, "RangeMax"))
                                else:
                                    # this range is too big to expand even if we want to
                                    if gotMultipleRanges:
                                        currentValues.append((thisRangeMin, thisRangeDesc, "MultiRangeMin"))
                                        currentValues.append((thisRangeMax, thisRangeDesc, "MultiRangeMax"))
                                    else:
                                        currentValues.append((thisRangeMin, thisRangeDesc, "RangeMin"))
                                        currentValues.append((thisRangeMax, thisRangeDesc, "RangeMax"))

                        if myItems[-1].has_key('Values'):
                                # occasionally items have two valueset chunks!
                            myItems[-1]['Values'].extend(currentValues)
                        else:
                            myItems[-1]['Values'] = currentValues
                        currentValues = []
                        #else:
                        #    raise ValueError("Error parsing valueset at line "+str(parsedLines))

                    elif currentChunkType == 'Relation':
                        relLink = myRelationProcessor.Emit()
                        relLink['FileCode'] = currentSurveyCode
                        #for relLink in chunkInfo['Relations']:
                        #    relLink['RelName'] = currentRelationshipName
                        #    relLink['PrimaryTable'] = currentRelationshipPrimary
                        myRelations.append(relLink)


                    # Otherwise we are at the end of a chunk defining an actual item (recode)
                    elif currentChunkType == 'Item':
                        if currentlyParsing == "Records":
                            # This is a "normal" line of the file, i.e. one recode or column of a table.
                            # Apply the parent hierarchical labels, just stored in simple globals
                            chunkInfo['RecordName'] = currentRecordName
                            chunkInfo['RecordLabel'] = currentRecordLabel
                            chunkInfo['RecordTypeValue'] = currentRecordType.strip("'")
                            chunkInfo['LevelName'] = currentLevelName
                            chunkInfo['LevelLabel'] = currentLevelLabel
                            chunkInfo['FileCode']=currentSurveyCode
                            if not 'ZeroFill' in chunkInfo:
                                chunkInfo['ZeroFill'] = currentSurveyZeroFill
                            if not 'DecimalChar' in chunkInfo:
                                chunkInfo['DecimalChar'] = currentSurveyDecChar
                            # "save" the information to the output list
                            chunkInfo['ItemType'] = 'Item'
                            myItems.append(chunkInfo)
                        elif currentlyParsing == "IdItems":
                            # this is a special case; it needs to be written out as an "item" for
                            # each record. In the .dcf, IdItems comes after level info but before record
                            # info. So save the info into dirty globals so that when we parse the record
                            # info that follows we have access to it.
                            currentIds.append({
                                'Name':     chunkInfo['Name'],
                                'Label':    chunkInfo['Label'],
                                'Start':    chunkInfo['Start'],
                                'Len':      chunkInfo['Len']
                            })

            else:
                # We are "within" a chunk of information
                # add item key / value to the current chunk dictionary
                # There are sometimes lines with more than one equals sign in (as it can appear in the
                # description) so split at the FIRST = position only and clear up a bit (carriage return)
                splitPos = line.find('=')
                fieldName = line[0:splitPos].strip()
                fieldVal = line[splitPos+1:].strip()
                #fieldName,fieldVal = line.split('=')

                if currentlyParsing == 'Relation':
                    addResult = myRelationProcessor.AddRow(fieldName, fieldVal)
                    if addResult is not None:
                        addResult['FileCode'] = currentSurveyCode
                        myRelations.append(addResult)

                elif fieldName == 'Value':
                    # we don't explicitly check that we're in a valueset chunk, but we will be(?)

                    # Look for a description first. Because if a description contains a time
                    # then this would be seen as a range below
                    # e.g. Value=1;Yes: between 2:00 and 6:00 pm
                    descMatch = re.split(';(.*)$', fieldVal)
                    if len(descMatch) > 1:
                        valDesc = descMatch[1]
                        fieldVal = descMatch[0]
                    else:
                        valDesc = ''

                    # match value ranges based on pattern "digits-colon-digits"
                    # Add these to a separate list of valueranges, because we will write them out differently
                    # depending on whether there is one or more than one range specified
                    match = re.search('-?\d+:-?\d+', fieldVal)
                    if match:
                        try:
                            # the right hand side sometimes contains a description of the range values
                            # after a semicolon
                            #descMatch = re.search('^(.*);(\w+)$', fieldVal)
                            #if descMatch:
                            #    valDesc = descMatch.group(1)
                            #else:
                            #    valDesc = ''

                            # again don't just split and unpack, in case there is a colon in the description too
                            # also sometimes we see multiple ranges on one line e.g. line 35629 of COIR53.DCF:
                            # 100:101 102:198;Days
                            rangesOnLine = re.findall('-?\d+:-?\d+', fieldVal)
                            for minmax in rangesOnLine:
                                splitPos = minmax.find(':')
                                vMin = minmax[0:splitPos].strip()
                                vMax = minmax[splitPos+1:].strip()
                                if not 'ValueRanges' in chunkInfo:
                                    chunkInfo['ValueRanges'] = []
                                chunkInfo['ValueRanges'].append((vMin, vMax, valDesc.strip()))

                            #if re.match('\d+;', vMax):
                            #    splitPos = vMax.find(';')
                            #    vMaxNew = vMax[0:splitPos]
                            #    valDesc = vMax[splitPos+1:]
                            #    vMax = vMaxNew
                            # if it doesn't then use the valueset label instead
                            #else:
                            #    valDesc = chunkInfo['Label']
                            #    valDesc = ''
                        except:
                            print "uhoh!"
                            print fieldVal
                            print chunkInfo

                            valRange, otherCrap = fieldVal.split(';')
                            vMin,vMax = valRange.split(':')
                            if not 'ValueRanges' in chunkInfo:
                                chunkInfo['ValueRanges'] = []
                            chunkInfo['ValueRanges'].append((vMin, vMax, valDesc.strip()))

                    # match "normal" value/description pairs based on digits-semicolon
                    #elif re.match('\d+', fieldVal):
                        # split at pos of first semicolon
                        #scPos = fieldVal.find(';')
                        #val = fieldVal[0:scPos]
                        #valDesc = fieldVal[scPos+1:]
                        # do not just split at semicolon because semicolon might also occur within the desc
                        #val, valDesc = fieldVal.split(';')
                    #    m = re.match('\d+', fieldVal)
                    #    val = m.group()
                    #    currentValues.append((val.strip(),valDesc.strip(), "ExplicitValue"))

                    # match horrible lines with VALUE given as string with quotes (see VCAL_VS1)
                    #elif re.match('.+;', fieldVal):
                    #    scPos = fieldVal.find(';')
                    #    val = fieldVal[0:scPos]
                    #    valDesc = fieldVal[scPos+1:]
                        #val, valDesc = fieldVal.split(';')
                    #    currentValues.append((val.strip("'").strip(),valDesc.strip("'").strip(), "ExplicitValue"))

                    # else save whatever we've got, presumably there is a value with no desc
                    else:
                        currentValues.append((fieldVal,valDesc.strip(), "ExplicitValue"))

                elif not chunkInfo.has_key(fieldName):
                    # append the first occurrence of other labels. Subsequent ones will be silently discarded
                    chunkInfo[fieldName] = fieldVal
        # For any columns that are mentioned in a relation, output them in the recordspec as being a joinable
        # column. We couldn't do this as we went along because the relations info is only parsed at the end.
        allJoinCols = defaultdict(set)
        for rel in myRelations:
            if rel["PrimaryLink"] != "*ROWID*":
                allJoinCols[rel["PrimaryTable"]].add(rel["PrimaryLink"])
            if rel["SecondaryLink"] != "*ROWID*":
                allJoinCols[rel["SecondaryTable"]].add(rel["SecondaryLink"])
        for item in myItems:
            if item['ItemType'] == 'Item':
                if (item['RecordName'] in allJoinCols and
                    item['Name'] in allJoinCols[item['RecordName']]):
                    item['ItemType'] = 'JoinableItem'

        print "Parsed {0!s} lines into {1!s} items".format(parsedLines,len(myItems))
        return (myItems, myRelations)


class RelationRowProcessor:
    ''' Maintains state necessary for sequential processing of [Relation] rows in DCF dictionary files

    All other parts of the DCF file have blank-line-delimited sections each of which maps to a single
    row in our output flat specification files. However the [Relation] sections may encode the
    information pertaining to several specification output rows within a single block. We therefore
    need this class to maintain state while processing the block and generate output information at
    the appropriate points.

    Each block defines joins from a single primary (left) table to one or many secondary (right)
    tables. Each side of each join can be between a column or the row index, based on whether a
    primarylink and/or secondarylink row is given.

    So overall a DCF block will start with "Primary", there may be then be 1 or more repetitions of join-groups
    each of which consists of of (0/1 * PrimaryLink, 1* Secondary, 0/1 * SecondaryLink)

    Usage:
    proc = RelationRowProcessor()
    proc.AddRow("Name","TestRelation") # returns None
    proc.AddRow("Primary", "RECH1") # None
    proc.AddRow("Secondary", "RECH4") # None
    proc.AddRow("SecondaryLink", "IDXH4") # None
    proc.AddRow("PrimaryLink", "HVIDX") # Returns the (occ)->item join specified over last 3 rows
    proc.AddRow("Secondary", "RECML") # None
    proc.AddRow("Secondary", "RECHMA") # Returns the item->(occ) join specified over last 2 rows
    proc.Emit() # Returns the (occ)->(occ) join specified over the last row

    '''
    def __init__(self):
        self.RelationshipName = ""
        self.PrimaryTable = ""
        self.PrimaryLink = ""
        self.SecondaryTable = ""
        self.SecondaryLink = ""

    def __GetReturnObj__(self):
        canEmit = True
        if self.RelationshipName == "" or self.PrimaryTable == "" or self.SecondaryTable == "":
            canEmit = False
        if canEmit:
            return {
                "RelName"       : self.RelationshipName,
                "PrimaryTable"  : self.PrimaryTable,
                "SecondaryTable" : self.SecondaryTable,
                "PrimaryLink"   : self.PrimaryLink if self.PrimaryLink != "" else "*ROWID*",
                "SecondaryLink" : self.SecondaryLink if self.SecondaryLink != "" else "*ROWID*"
            }
        else:
            return None
    def AddRow(self, fieldname, fieldvalue):
        ''' Adds a DCF file row defining part of a join and returns the join details if it's complete.

        When a row is added that that defines the start of a new join specification, then the details
        of the prior (now complete) join will be returned. The return will be None if the join is not
        row added does not define the start of a new join. For the last join defined in a [Relation]
        block you will therefore need to force emitting the row with Emit().
        '''
        # the end of a join specification can be marked by:
        # - SecondaryLink -> always implies the end of a join
        # - Secondary -> implies the end of a join if it is followed by PrimaryLink or blank
        if fieldname == "Name":
            if self.RelationshipName != "":
                raise ValueError("Name is already set, data would be lost. Use Emit() first")
            self.RelationshipName = fieldvalue
            return
        elif fieldname == "Primary":
            # A block defines joins from a single primary table. If it's already set, then this implies
            # we haven't retrieved the last bit of info (using Emit()) of the last block.
            if self.PrimaryTable != "":
                raise ValueError("Primary Table is already set, data would be lost. Use Emit() first")
            self.PrimaryTable = fieldvalue
            return
        elif fieldname == "PrimaryLink":
            # This may be the start of the definition of a new join row, meaning that the prior (current) state
            # represents a complete join that should be returned. Or we may be defining the first
            # one.
            returnObj = self.__GetReturnObj__()
            self.PrimaryLink = fieldvalue
            # if we get a new primarylink then any existing secondary and secondarylink info are obsoleted
            self.SecondaryTable = ""
            self.SecondaryLink = ""
            return returnObj
        elif fieldname == "Secondary":
            # This may be the start of the definition of a new join row, meaning that the prior (current) state
            # represents a complete join that should be returned. Or we may be defining the first
            # one.
            returnobj = self.__GetReturnObj__()
            if self.SecondaryTable != "":
                # this isn't the first secondary table since the last primary link row. In other words there was
                # one lot of PrimaryLink -> Secondary +- SecondaryLink, and now there is another Secondary;
                # primarylink was not given and so we have just finished specifying an "occ" relationship on the primary side
                # This means that we are onto a new relationship row, i.e. need to emit the info about the previous one.
                self.PrimaryLink = ""
            self.SecondaryTable = fieldvalue
            # If we get a new Secondary table then any existing secondarylink info are obsoleted
            self.SecondaryLink = ""
            return returnobj
        elif fieldname == "SecondaryLink":
            self.SecondaryLink = fieldvalue
        else:
            raise ValueError("Unknown relationship specification tag "+fieldvalue)

    def Emit(self):
        ''' Returns the join currently specified, if currently possible, and resets the processor '''
        obj = self.__GetReturnObj__()
        self.__init__()
        return obj