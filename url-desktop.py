#!/usr/bin/env python3

import argparse
import os

#---------------------------------------------------------------------------------------------------
# Usage Notes & Examples (run with -h for full help):
#---------------------------------------------------------------------------------------------------
#
# Convert all .url files in current directory:
#  $ url-desktop.py
#
# Convert all .desktop files in current directory:
#  $ url-desktop.py --desktop-to-url
#
#---------------------------------------------------------------------------------------------------
# Dependencies & Manuals:
#---------------------------------------------------------------------------------------------------
#
# argparse:     https://docs.python.org/3/library/argparse.html
#


def get_url(f, extension, output=True):
    if f not in os.listdir():
        if output: print('WARNING: Ignoring file %s which was not found in current directory.' % f)
        return ''
    if not f.endswith(extension):
        if output:
            print('WARNING: Ignoring file %s which does not have %s extension.' % (f, extension))
        return ''
    with open(f, 'r') as oldf:
        for line in oldf:
            if 'URL=' in line: return line.strip().split('=')[1]
        else:
            if output: print('WARNING: Ignoring file %s which does not contain URL' % f)
            return ''

def convert(args):
    if args.input_files == []: input_files = os.listdir()
    else: input_files = args.input_files

    for f in input_files:
        if args.dest_type == 'desktop':
            url = get_url(f, '.url', args.input_files != [])
            site = f[:-4]
        else:
            url = get_url(f, '.desktop', args.input_files != [])
            site = f[:-8]

        if url == '': continue

        with open(site + '.' + args.dest_type, 'w') as newf:
            if args.dest_type == 'desktop':
                newf.write(('[Desktop Entry]\nEncoding=UTF-8\nName=%s\n' +
                        'Type=Link\nURL=%s\nIcon=firefox\nName[en-ZA]=%s') % (site, url, site))
            else:
                newf.write(('[{000214A0-0000-0000-C000-000000000046}]\n' +
                        'Prop3=19,11\n[InternetShortcut]\nIDList=\nURL=%s') % url)


if __name__ == '__main__':
    # Create argument parser:
    parser = argparse.ArgumentParser(description='Convert internet shortcuts from Windows format \
            (.url) to Ubuntu format (.desktop) or vice-versa.')
    parser.add_argument('--desktop-to-url', action='store_const', dest='dest_type', const='url',
            default='desktop', help='convert from .desktop to .url (default converts .url to \
                    .desktop)')
    parser.add_argument('input_files', metavar='FILE', nargs='*',
            help='input file, which must have the correct extension for the current conversion \
                    mode, or it is ignored (if omitted, convert all relevant files in current \
                    directory)')

    # Parse args and run command:
    args = parser.parse_args()
    convert(args)
