#!/usr/bin/env python3

import argparse
import os
import shutil
import sys
import filecmp
from itertools import count

#---------------------------------------------------------------------------------------------------
# Usage Notes & Examples (run with -h for full help):
#---------------------------------------------------------------------------------------------------
#
# Distribute & compare contents to ~/Music
#  $ distribute.py
#
# Distribute without comparing contents to ~/Music
#  $ distribute.py --nocompare
#


def get_path(i, f = ''):
    ''' Returns path of ix255, parent if i==0 or ~/Music if i<0. If f is non-empty, it specifies a
    file within this folder. '''
    gpath = os.path.dirname(os.path.realpath(sys.argv[0]))
    if i == 0: path = gpath
    elif i < 0: path = os.environ['HOME'] + '/Music'
    else: path = gpath + '/' + str(i) + 'x255'
    if f == '': return path
    return path + '/' + f

def files(i):
    ''' Returns files in get_path(i). '''
    return sorted(os.listdir(get_path(i)), key=str.lower)

def move(f, i, j):
    ''' Moves file with name f from get_path(i) to get_path(j). '''
    global moved
    try: os.mkdir(get_path(j))
    except: pass
    shutil.move(get_path(i, f), get_path(j, f))
    moved[f] = 'parent' if j == 0 else str(j) + 'x255'

def check_synched(f, i, compare = True):
    ''' Check that file given by get_path(i, f) corresponds to one in ~/Music '''
    if not os.path.isfile(get_path(-1, f)):
        print('No file named "' + f + '" exists in ~/Music')
        if input('Remove file? [y/n]: ') in 'yY':
            os.remove(get_path(i, f))
            print('File removed.')
    elif compare and not filecmp.cmp(get_path(-1, f), get_path(i, f)):
        print('File "' + f + '" differs from that in ~/Music')
        if input('Update file? [y/n]: ') in 'yY':
            shutil.copy(get_path(-1, f), get_path(i, f))
            print('File updated.')

moved = {} # Stores final destinations of files moved
parent_files = files(0)

if __name__ == '__main__':
    # Create argument parser and parse args
    parser = argparse.ArgumentParser(description='Sorts .mp3 files in containing directory \
            alphabetically into sub-directories "1x255", "2x255", ..., each with 255 files. \
            Outputs all files moved, and whether any .mp3 files are not present with the same name \
            and content in ~/Music (as might happen if a song is edited). This is designed to \
            divide music for the Toyota Yarris sound system. The script assumes that existing \
            files in "1x255", "2x255", ... are already sorted alphabetically. Thus only the first \
            or last files in each directory are ever moved.')
    parser.add_argument('--nocompare', action='store_false', dest='compare',
            help='do not compare contents of files to those in ~/Music')

    args = parser.parse_args()

    # Move all .mp3 files from parent into 1x255
    for f in parent_files:
        if f[-4:] == '.mp3': move(f, 0, 1)

    # Redistribute files & check against ~/Music
    for i in count(1):
        if str(i) + 'x255' in parent_files:
            ifiles = files(i)
            for f in ifiles: check_synched(f, i, args.compare)

            n = len(ifiles)
            if n > 255:
                for f in ifiles[255:]: move(f, i, i + 1)
            elif n < 255 and str(i + 1) + 'x255' in parent_files:
                for f in files(i + 1)[:255 - n]: move(f, i + 1, i)
        else: break

    # Report changes (a single file moved many times is reported once)
    for f in moved: print('Moved "' + f + '" into ' + moved[f])

