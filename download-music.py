#!/usr/bin/env python3

import argparse
import subprocess
from sys import exit
import os

#---------------------------------------------------------------------------------------------------
# Usage Notes & Examples (run with -h for full help):
#---------------------------------------------------------------------------------------------------
#
# Expects a song list in folder of execution, which is ~/Downloads unless run with --wd. This file
# is given by --song-list (defaults to "song-list.txt"), and should contain URLs of all videos to be
# downloaded, separated by newlines. The song list is cleared on success unless run with --noclear.
# The script will also trim leading & trailing silence from all .mp3 files in folder of execution
# unless run with --notrim.
#
# Many more sites than YouTube are supported, including SoundCloud, Bandcamp, & others listed at:
#  https://rg3.github.io/youtube-dl/supportedsites.html
#
#---------------------------------------------------------------------------------------------------
# Dependencies & Manuals:
#---------------------------------------------------------------------------------------------------
#
# sox           http://sox.sourceforge.net/sox.html
# youtube-dl    https://rg3.github.io/youtube-dl/
# argparse      https://docs.python.org/3/library/argparse.html
#


def check_dir(wd, song_list, file_needed):
    ''' Change directory & check for download list if needed '''
    if not wd: os.chdir(os.path.expanduser('~/Downloads'))

    if file_needed and not os.path.isfile(song_list):
        print('ERROR: Could not find song list "%s"' % song_list)
        exit(1)

def download(song_list, quiet, force, playlist):
    ''' Download all songs listed in song_list using youtube-dl, with given parameters '''
    # Set up download arguments
    dl_args = ['--extract-audio', '--audio-format', 'mp3', '--audio-quality', '0', '--geo-bypass']
    if quiet: dl_args += ['-q', '--console-title']
    if force: dl_args.append('--ignore-errors')
    if playlist: dl_args.append('--yes-playlist')
    else: dl_args.append('--no-playlist')
    dl_args += ['--default-search', 'ytsearch', '-a', song_list, '--output', '%(title)s.%(ext)s']

    # Download & quit with error if download fails
    if subprocess.run(['youtube-dl'] + dl_args).returncode != 0:
        print('ERROR: Could not download from list')
        exit(1)

def trim_mp3s(quiet):
    ''' Trim silence from beginning and end of all .mp3 files in current directory '''
    # Loop through .mp3 files, trimming silence
    for f in os.listdir('.'):
        if f.endswith('.mp3'):
            exit1 = subprocess.run(['sox', "%s" % f, '_tmpsoxout.mp3', 'reverse',
                'silence', '1', '0.1', '0.1%', 'reverse']).returncode
            exit2 = subprocess.run(['sox', '_tmpsoxout.mp3', "%s" % f, 'silence',
                '1', '0.1', '0.1%']).returncode

            # Report success or failure
            if exit1 == 0 and exit2 == 0:
                if not quiet: print('Silence trimmed from %s' % f)
            else: print('ERROR: Could not trim silence from %s' % f)

    # Remove temporary file _tmpsoxout.mp3
    if os.path.isfile('_tmpsoxout.mp3'):
        if subprocess.run(['rm', '_tmpsoxout.mp3']).returncode == 0 and not quiet:
            print('Temporary file deleted')

if __name__ == '__main__':
    # Create argument parser and parse args
    parser = argparse.ArgumentParser(description='Downloads .mp3 files from YouTube, Soundcloud,\
            Bandcamp and more, using youtube-dl (https://rg3.github.io/youtube-dl/), and trims\
            silence from downloaded files. URLs of songs to download should be given in the\
            file specified by --song-list, separated by newlines. All supported websites are\
            listed at https://rg3.github.io/youtube-dl/supportedsites.html')
    parser.add_argument('--song-list', action='store', dest='song_list', default='song-list.txt',
            metavar='FILE', help='file containing URLs to download (defaults to "song-list.txt")')
    parser.add_argument('--playlist', action='store_true',
            help='download all songs in any playlists linked to by URLs')
    parser.add_argument('--force', action='store_true', help='do not abort if download fails')
    parser.add_argument('--quiet', action='store_true', help='suppress output of download process')
    parser.add_argument('--wd', action='store_true',
            help='run in working directory rather than ~/Downloads (must contain song list)')

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--notrim', action='store_false', dest='trim',
            help='do not trim silence from .mp3 files in folder of execution')
    group.add_argument('--nodownload', action='store_false', dest='download',
            help='do not download or clear download list (only trim silence)')
    parser.add_argument('--noclear', action='store_false', dest='clear',
            help='do not clear download list (set by default if --nodownload given)')

    args = parser.parse_args()

    # Execute relevant functions
    try:
        check_dir(args.wd, args.song_list, args.download and args.clear)

        if args.download:
            download(args.song_list, args.quiet, args.force, args.playlist)

            if args.clear:
                with open(args.song_list, 'w') as f: f.write('')
                if not args.quiet: print('Download list cleared')

        if args.trim: trim_mp3s(args.quiet)
    except KeyboardInterrupt:
        print('\nINTERRUPTED: Terminating. Some files may not be correctly removed.')

