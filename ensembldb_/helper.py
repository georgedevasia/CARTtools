import os
import sys
import urllib
from operator import itemgetter
import gzip
import pysam


# Process Ensembl data
def process_data(options, genome_build):

    # Dictionary of Gene objects
    genesdata = dict()

    # Load custom transcript IDs
    transIDs = set()
    if options.input is not None:
        transIDs = readTranscriptIDs(options.input)
        print '\n' + str(len(transIDs)) + ' transcripts read from ' + options.input + ' are considered'

    # Load candidate and CCDS data for Ensembl <75
    candidates = dict()
    if options.ensembl < 75:
        datadir = os.path.dirname(os.path.realpath(__file__)) + '/data'
        for line in open(datadir+'/info'+str(options.ensembl) + '.txt'):
            line = line.strip()
            if line == '':
                continue
            cols = line.split('\t')
            if cols[0] not in candidates.keys():
                candidates[cols[0]] = dict()
            candidates[cols[0]][cols[1]] = int(cols[2])

    # Download Ensembl data
    sys.stdout.write('\nDownloading Ensembl database... ')
    sys.stdout.flush()
    url = 'ftp://ftp.ensembl.org/pub/release-' + str(options.ensembl) + '/gtf/homo_sapiens/Homo_sapiens.' + genome_build + '.' + str(options.ensembl) + '.gtf.gz'
    try:
        urllib.urlretrieve(url, 'ensembl_data.gz')
    except:
        print '\n\nCannot connect to Ensembl FTP site. No internet connection?\n'
        quit()

    sys.stdout.write('OK\n')

    # Iterate through the lines in the ensembl data file
    sys.stdout.write('Extracting transcript data... ')
    sys.stdout.flush()
    first = True
    prevenst = ''
    transcript = None
    for line in gzip.open('ensembl_data.gz', 'r'):
        line = line.strip()
        if line.startswith('#'):
            continue
        cols = line.split('\t')

        # Only consider transcripts on the following chromosomes
        if cols[0] not in ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15', '16', '17', '18', '19', '20', '21', '22', '23', 'MT', 'X', 'Y']: continue

        # Consider only certain types of lines
        if cols[2] not in ['exon','transcript','start_codon','stop_codon']:
            continue

        # Annotation tags
        tags = cols[8].split(';')
        # Retrieve transcript ID
        enst = getValue(tags, 'transcript_id')

        # Do not consider transcript if it is not on the custom transcript list
        if options.input is not None and enst not in transIDs:
            continue

        # Finalize and output transcript object
        if not enst == prevenst:

            # Finalize transcript and add to Gene object if candidate
            if not first:
                transcript.finalize()
                if transcript.isCandidate():
                    if transcript.ENSG not in genesdata.keys(): genesdata[transcript.ENSG] = Gene(transcript.GENE, transcript.ENSG)
                    genesdata[transcript.ENSG].TRANSCRIPTS[transcript.ENST] = transcript

            # Initialize new Transcript object
            transcript = Transcript()
            transcript.ENST = enst
            transcript.GENE = getValue(tags, 'gene_name')
            transcript.ENSG = getValue(tags, 'gene_id')
            transcript.CHROM = cols[0]
            if cols[6] == '+':
                transcript.STRAND = '1'
            else:
                transcript.STRAND = '-1'

            # Retrieve gene biotype and transcript biotype
            transcript.GENETYPE = getValue(tags, 'gene_type')
            if transcript.GENETYPE is None:
                transcript.GENETYPE = getValue(tags, 'gene_biotype')
            transcript.TRANSTYPE = getValue(tags, 'transcript_type')
            if transcript.TRANSTYPE is None:
                transcript.TRANSTYPE = getValue(tags, 'transcript_biotype')
            if transcript.TRANSTYPE is None:
                transcript.TRANSTYPE = cols[1]

        # If line represents an exon
        if cols[2] == 'exon':
            idx = 0
            for x in tags:
                x = x.strip()
                if x.startswith('exon_number'):
                    s = x[x.find('\"') + 1:]
                    idx = int(s[:s.find('\"')]) - 1
                    break
            start = int(cols[3]) - 1
            end = int(cols[4])
            if idx >= len(transcript.EXONS):
                for _ in range(len(transcript.EXONS), idx + 1): transcript.EXONS.append(None)
            transcript.EXONS[idx] = Exon(start, end)

        if cols[2] == 'start_codon':
            if transcript.STRAND == '1':
                if transcript.CODING_START is None or int(cols[3]) < transcript.CODING_START: transcript.CODING_START = int(cols[3])
            else:
                if transcript.CODING_START is None or int(cols[4]) > transcript.CODING_START: transcript.CODING_START = int(cols[4])

        if cols[2] == 'stop_codon':
            if transcript.STRAND == '1':
                if transcript.CODING_END is None or int(cols[4]) > transcript.CODING_END: transcript.CODING_END = int(cols[4])
            else:
                if transcript.CODING_END is None or int(cols[3]) < transcript.CODING_END: transcript.CODING_END = int(cols[3])

        # Check if transcript is complete and is a CCDS transcript
        if transcript.isComplete is None:
            if int(options.ensembl) < 75:
                if transcript.ENST in candidates[transcript.CHROM].keys():
                    transcript.CCDS = (candidates[transcript.CHROM][transcript.ENST] == 1)
                    transcript.isComplete = True
                else:
                    transcript.isComplete = False
            else:
                transcript.isComplete = not (getBooleanValue(tags, 'cds_start_NF') or getBooleanValue(tags, 'cds_end_NF'))
                if getValue(tags, 'ccds_id') is not None: transcript.CCDS=True
                else: transcript.CCDS=False

        prevenst = enst
        if first: first = False

    # Finalize last transcript and add to Gene object if candidate
    if transcript is not None:
        transcript.finalize()
        if transcript.isCandidate():
            if transcript.ENSG not in genesdata.keys():
                genesdata[transcript.ENSG] = Gene(transcript.GENE, transcript.ENSG)
            genesdata[transcript.ENSG].TRANSCRIPTS[transcript.ENST] = transcript

    # If no transcript ID from the input file was found in the Ensembl release
    if len(genesdata) == 0:
        print '\n\nNo transcripts from '+options.input+' found in Ensembl release.'
        print '\nNo transcript database created.'
        print "-----------------------------------------------------------------\n"
        os.remove('ensembl_data.gz')
        quit()

    # Initialize temporary output file
    outfile = open('temp.txt', 'w')

    # Initialize output list file if needed
    outfile_list = open(options.output+'.txt','w')
    outfile_list.write('#ENSG\tGENE\tENST\n')

    # Output transcripts of each gene
    for ensg, gene in genesdata.iteritems():
        gene.output(outfile,outfile_list)

    # Close temporary output files
    outfile.close()
    outfile_list.close()

    # Sort temporary output file
    data = dict()
    counter = 0
    for line in open('temp.txt'):
        if not line.startswith('ENST'): continue
        counter += 1
        line.rstrip()
        record = line.split('\t')
        record[6] = int(record[6])
        if record[4] in data.keys():
            data[record[4]].append(record)
        else:
            data[record[4]] = []
            data[record[4]].append(record)

    sys.stdout.write('OK\n')
    sys.stdout.write('Sorting transcripts... ')
    sys.stdout.flush()
    sortedRecords = sortRecords(data, 6, 7)
    writeToFile(sortedRecords, options.output)

    # Remove temporary files
    sys.stdout.write('OK\n')
    sys.stdout.write('Removing temporary files... ')
    sys.stdout.flush()
    os.remove('temp.txt')
    os.remove('ensembl_data.gz')
    sys.stdout.write('OK\n')

    # Return sorted records
    return len(sortedRecords)


# Retrieve tag value
def getValue(tags, tag):
    ret=None
    for x in tags:
        x = x.strip()
        if x.startswith(tag):
            s = x[x.find('\"') + 1:]
            ret = s[:s.find('\"')]
            break
    return ret


# Retrieve boolean tag value
def getBooleanValue(tags, tag):
    for x in tags:
        x = x.strip()
        if x.startswith('tag'):
            s = x[x.find('\"') + 1:]
            value = s[:s.find('\"')]
            if value==tag: return True
    return False


# Read transcript IDs from file
def readTranscriptIDs(inputfn):
    ret = set()
    for line in open(inputfn): ret.add(line.strip())
    return ret


# Sort records in file
def sortRecords(records, idx1, idx2):
    ret = []
    chroms = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15', '16', '17', '18', '19', '20', '21', '22', '23', 'MT', 'X', 'Y']
    for i in range(len(chroms)):
        chrom = chroms[i]
        if chrom in records.keys():
            records[chrom] = sorted(records[chrom], key=itemgetter(idx1,idx2))
    for i in range(len(chroms)):
        chrom = chroms[i]
        if chrom in records.keys():
            for record in records[chrom]: ret.append(record)
    return ret


# Class representing a transcript
class Transcript(object):

    # Constructor
    def __init__(self):
        self.ENST = None
        self.GENE = None
        self.ENSG = None
        self.CHROM = None
        self.STRAND = None
        self.POS = None
        self.POSEND = None
        self.GENETYPE = None
        self.TRANSTYPE = None
        self.CODING_START = None
        self.CODING_END = None
        self.CODING_START_RELATIVE = None
        self.CCDS = None
        self.EXONS = []
        self.PROTL = None
        self.CDNAL = None
        self.isComplete = None


    # Get summary information about the transcript
    def getInfoString(self):
        if self.STRAND == '1': ret = '+/'
        else: ret = '-/'
        cdna = self.getcDNALength()
        return ret+str(round((self.POSEND-self.POS+1)/1000,1))+'kb/'+str(len(self.EXONS))+'/'+str(round(cdna/1000,1))+'kb/'+str(self.getProteinLength())


    # Get cDNA length of the transcript
    def getcDNALength(self):
        ret = 0
        for exon in self.EXONS: ret += exon.END - exon.START
        return ret


    # Get protein length of the transcript
    def getProteinLength(self):
        codingdna = 0
        if self.STRAND == '1':
            for exon in self.EXONS:
                if exon.END < self.CODING_START:
                    continue
                if exon.START > self.CODING_END:
                    continue
                if exon.START <= self.CODING_START <= exon.END:
                    start = self.CODING_START
                else:
                    start = exon.START + 1
                if exon.START <= self.CODING_END <= exon.END:
                    end = self.CODING_END
                else:
                    end = exon.END
                codingdna += end - start + 1
        else:
            for exon in self.EXONS:
                if exon.START > self.CODING_START:
                    continue
                if exon.END < self.CODING_END:
                    continue
                if exon.START <= self.CODING_START <= exon.END: end = self.CODING_START
                else: end = exon.END
                if exon.START <= self.CODING_END <= exon.END: start = self.CODING_END
                else: start = exon.START + 1
                codingdna += end - start + 1
        return int((codingdna - 3) / 3)


    # Check if it is a candidate transcript
    def isCandidate(self):
        if not (self.GENETYPE=='protein_coding' and self.TRANSTYPE=='protein_coding'): return False
        return (self.CODING_START is not None and self.CODING_END is not None) and self.isComplete


    # Output transcript
    def output(self, outfile, outfile_list):
        out = self.ENST + '\t' + self.GENE + '\t' + self.ENSG + '\t' + self.getInfoString() + '\t' + self.CHROM + '\t' + self.STRAND + '\t' + str(self.POS)
        out += '\t' + str(self.POSEND) + '\t' + str(self.CODING_START_RELATIVE) + '\t' + str(self.CODING_START)
        out += '\t' + str(self.CODING_END)
        for exondata in self.EXONS: out += '\t' + str(exondata.START) + '\t' + str(exondata.END)
        outfile.write(out + '\n')
        outfile_list.write(self.ENSG+'\t'+self.GENE+'\t'+self.ENST+'\n')


    # Finalize transcript
    def finalize(self):
        if self.STRAND == '1':
            self.POS = self.EXONS[0].START
            self.POSEND = self.EXONS[len(self.EXONS) - 1].END
            codingStartRelative = 0
            for exondata in self.EXONS:
                if exondata.START <= self.CODING_START <= exondata.END:
                    codingStartRelative += self.CODING_START - exondata.START
                    break
                else:
                    codingStartRelative += exondata.END - exondata.START
            self.CODING_START_RELATIVE = codingStartRelative
        else:
            self.POS = self.EXONS[len(self.EXONS) - 1].START
            self.POSEND = self.EXONS[0].END
            codingStartRelative = 0
            for exondata in self.EXONS:
                if exondata.START <= self.CODING_START <= exondata.END:
                    codingStartRelative += exondata.END - self.CODING_START + 1
                    break
                else:
                    codingStartRelative += exondata.END - exondata.START
            self.CODING_START_RELATIVE = codingStartRelative
        self.PROTL = self.getProteinLength()
        self.CDNAL = self.getcDNALength()


# Class representing an exon
class Exon(object):

    # Constructor
    def __init__(self, start, end):
        self.START = start
        self.END = end


# Class representing a gene
class Gene(object):

    # Constructor
    def __init__(self, symbol, ensg):
        self.SYMBOL = symbol
        self.ENSG = ensg
        self.TRANSCRIPTS = dict()

    # Output all transcripts
    def output(self, outfile, outfile_list):
        for _, transcript in self.TRANSCRIPTS.iteritems():
            transcript.output(outfile, outfile_list)


# Write records to file
def writeToFile(sortedRecords, filename):
    outfile = open(filename, 'w')
    for record in sortedRecords:
        s = str(record[0]).rstrip()
        for i in range(1, len(record)): s += '\t' + str(record[i]).rstrip()
        outfile.write(s + '\n')
    outfile.close()


# Read records from file as a list
def readRecords(inputfn):
    ret = []
    for line in open(inputfn): ret.append(line.strip())
    return ret


# Use Tabix to index output file
def indexFile(options):
    sys.stdout.write('Compressing output file... ')
    sys.stdout.flush()
    pysam.tabix_compress(options.output, options.output + '.gz', force=True)
    sys.stdout.write('OK\n')
    sys.stdout.write('Indexing output file... ')
    sys.stdout.flush()
    pysam.tabix_index(options.output + '.gz', seq_col=4, start_col=6, end_col=7, meta_char='#', force=True)
    sys.stdout.write('OK\n')