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


def get_url(f, extension):
    ''' Given a filename, f, if the file has the correct extension (.url or .desktop),
    extract the URL contained in the file '''
    if f not in os.listdir():
        print('WARNING: Ignoring file %s which was not found in current directory.' % f)
        return ''
    if not f.endswith(extension):
        print('WARNING: Ignoring file %s which does not have %s extension.' % (f, extension))
        return ''
    with open(f, 'r') as oldf:
        for line in oldf:
            if 'URL=' in line: return line.strip().split('=')[1]
        else:
            print('WARNING: Ignoring file %s which does not contain URL' % f)
            return ''

def convert(input_files, desktop_to_url = False):
    ''' Convert all .url files in input_files (list of file names) to .desktop, or vice-versa if
    desktop_to_url is True, ignoring files without correct extension '''
    if desktop_to_url: ext = '.desktop'
    else: ext = '.url'

    if input_files == []: input_files = filter(lambda f: f.endswith(ext), os.listdir())

    for f in input_files:
        url = get_url(f, ext)
        if url == '': continue

        if desktop_to_url: site = f[:-8]
        else: site = f[:-4]

        with open(site + '.' + ('url' if desktop_to_url else 'desktop'), 'w') as newf:
            if desktop_to_url:
                newf.write(('[{000214A0-0000-0000-C000-000000000046}]\n' +
                        'Prop3=19,11\n[InternetShortcut]\nIDList=\nURL=%s') % url)
            else:
                newf.write(('[Desktop Entry]\nEncoding=UTF-8\nName=%s\n' +
                        'Type=Link\nURL=%s\nIcon=firefox\nName[en-ZA]=%s') % (site, url, site))

if __name__ == '__main__':
    # Create argument parser:
    parser = argparse.ArgumentParser(description='Convert internet shortcuts from Windows format \
            (.url) to Ubuntu format (.desktop) or vice-versa.')
    parser.add_argument('--desktop-to-url', action='store_true', dest='desktop_to_url',
            help='convert from .desktop to .url (default converts .url to .desktop)')
    parser.add_argument('input_files', metavar='FILE', nargs='*',
            help='input file, which must have the correct extension for the current conversion \
                    mode, or it is ignored (if omitted, convert all relevant files in current \
                    directory)')

    # Parse args and run command:
    args = parser.parse_args()
    convert(args.input_files, args.desktop_to_url)
