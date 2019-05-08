#!/usr/bin/env python3

import argparse
import subprocess
import re

#---------------------------------------------------------------------------------------------------
# Usage Notes & Examples (run with -h for full help):
#---------------------------------------------------------------------------------------------------
#
# Merge pdfs:
#  $ pdftools.py splice file1.pdf 2,7-10 file2.pdf output.pdf --metadata=file1.pdf
#
# Extract pages:
#  $ pdftools.py splice file1.pdf 2-5 output.pdf
#
# Remove watermark:
#  $ pdftools.py replace "watermark" input.pdf output.pdf
#
# Rotate page range:
#  $ pdftools.py rotate input.pdf output.pdf --pages="1,3,5-9" --angle="-90"
#
#---------------------------------------------------------------------------------------------------
# Dependencies & Manuals:
#---------------------------------------------------------------------------------------------------
#
# qpdf          /usr/share/doc/qpdf/qpdf-manual.html
# argparse      https://docs.python.org/3/library/argparse.html
#


def replace(args):
    ''' Find & replace text in a pdf, most notably to remove a watermark '''
    # Decompress object streams in pdf file into more readable "qdf" format
    subprocess.run(['qpdf', '--qdf', '--object-streams=disable', args.input_file, '_temp1.pdf'])

    # Build regex pattern to find text, possibly ignoring qdf formatting characters
    if args.verbatim: regex = re.escape(args.find_text)
    else: regex = '(\)-?[0-9]*\()?'.join(list(args.find_text.replace(' ', '')))

    if args.ignore_case: pattern = re.compile(bytes(regex, encoding='utf-8'), re.IGNORECASE)
    else: pattern = re.compile(bytes(regex, encoding='utf-8'))

    # Perform replacements
    with open('_temp1.pdf', 'rb') as temp: tempcontent = temp.read()
    sub = pattern.subn(bytes(args.replace_text, encoding='utf-8'), tempcontent)
    print('SUCCESS: %s replacements made.' % sub[1])
    with open('_temp1.pdf', 'wb') as temp: temp.write(sub[0])

    # Repair damage to qdf file, such as now-incorrect data stream lengths
    with open('_temp2.pdf', 'w') as temp: subprocess.run(['fix-qdf', '_temp1.pdf'], stdout=temp)

    # Recompress qdf into final pdf, & remove temporary files
    subprocess.run(['qpdf', '--stream-data=compress', '_temp2.pdf', args.output_file])
    subprocess.run(['rm', '_temp1.pdf', '_temp2.pdf'])

def splice(args):
    ''' Collect specified pages from any number of pdfs into a single pdf '''
    subprocess.run(['qpdf', args.metadata, '--pages', *args.input_files, '--', args.output_file])

def rotate(args):
    ''' Rotate specified pages of pdf by specified angle '''
    subprocess.run(['qpdf', args.input_file, args.output_file, '--rotate=' + args.angle + ':' + args.pages])

if __name__ == '__main__':
    # Top-level parser:
    parser = argparse.ArgumentParser(description='Some tools for editing and combining pdfs. \
            Functionality is based on qpdf. In some cases, this tool just functions as a minimal \
            (and hence accessible) front-end to qpdf, while some functionality is more new.')
    subparsers = parser.add_subparsers(title='functions')

    # Parser for "splice":
    parser_splice = subparsers.add_parser('splice',
            help='collect specified pages from any number of pdfs into a single pdf')
    parser_splice.add_argument('--metadata', metavar='FILE', default='--empty',
            help='copy global data like bookmarks or fillable inputs from this file \
                    (default is empty pdf); can cause issues like broken bookmarks')
    parser_splice.add_argument('input_files', metavar='INPUT', nargs='+',
            help='input file, followed by an optional page range like 1-6,8')
    parser_splice.add_argument('output_file', metavar='OUTPUT', help='output file')
    parser_splice.set_defaults(func=splice)

    # Parser for "replace":
    parser_replace = subparsers.add_parser('replace', help='find and replace text in pdf')
    parser_replace.add_argument('--replace-text', dest='replace_text', metavar='TEXT', default='',
            help='replace any matches with this text (default is empty string)')
    parser_replace.add_argument('--verbatim', action='store_true', help='match text verbatim \
            (default ignores formatting characters in qdf)')
    parser_replace.add_argument('--ignore-case', dest='ignore_case', action='store_true',
            help='ignore case when searching (default is case-sensitive)')
    parser_replace.add_argument('find_text', metavar='FIND_TEXT', help='text to search for in input pdf')
    parser_replace.add_argument('input_file', metavar='INPUT', help='input file')
    parser_replace.add_argument('output_file', metavar='OUTPUT', help='output file')
    parser_replace.set_defaults(func=replace)

    # Parser for "rotate":
    parser_rotate = subparsers.add_parser('rotate',
            help='rotate pages in pdf; can cause issues with some elements being incorrectly placed, \
                    such as with fillable form inputs')
    parser_rotate.add_argument('--angle', metavar='ANGLE', default='+90',
            help='angle to rotate (90, 180 or 270); if preceded by + or -, given angle is added to \
                    or subtracted from current angle (default is +90)')
    parser_rotate.add_argument('--pages', metavar='PAGES', default='1-z',
            help='range of pages in input file to rotate (default is all pages)')
    parser_rotate.add_argument('input_file', metavar='INPUT', help='input file')
    parser_rotate.add_argument('output_file', metavar='OUTPUT', help='output file')
    parser_rotate.set_defaults(func=rotate)

    # Parse args and run command:
    args = parser.parse_args()
    if len(vars(args)) > 0: args.func(args)
    else: print('ERROR: No function selected. Run with -h flag to view arguments.')
