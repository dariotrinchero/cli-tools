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
# In this script, "parent" refers to the containing directory of this file.
#
# Distribute & compare contents to ~/Music
#  $ distribute.py
#
# Distribute without comparing contents to ~/Music
#  $ distribute.py --nocompare
#


def get_path(i, f = None):
    ''' Returns path of subdirectory ix255, or containing directory if i is 0, or ~/Music if i < 0;
    if f is given, it specifies a particular file in this directory '''
    gpath = os.path.dirname(os.path.realpath(sys.argv[0]))

    if i == 0: path = gpath
    elif i < 0: path = os.environ['HOME'] + '/Music'
    else: path = gpath + '/' + str(i) + 'x255'

    if f == None: return path
    return path + '/' + f

def files(i):
    ''' Returns list of name of all files in subdirectory ix255 '''
    return sorted(os.listdir(get_path(i)), key=str.lower)

def file_lists():
    ''' Generator to iterate through all subdirectories, yielding number of subdirectory and list
    of contained files for each '''
    parent_files = files(0)
    for i in count(1):
        if str(i) + 'x255' in parent_files: yield i, files(i)
        else: break

def move(f, i, j):
    ''' Moves file with name f from subdirectory ix255 to jx255 '''
    global moved
    try: os.mkdir(get_path(j))
    except: pass
    shutil.move(get_path(i, f), get_path(j, f))
    moved[f] = 'parent' if j == 0 else str(j) + 'x255'

def redistribute():
    ''' Redistribute files so that each subdirectory contains at most 255 files, filling from
    lower-numbered directories upwards '''
    global moved
    parent_files = files(0)
    print('Redistributing files ...')

    for i, ifiles in file_lists():
        n = len(ifiles)
        if n > 255:
            for f in ifiles[255:]: move(f, i, i + 1)
        elif n < 255 and str(i + 1) + 'x255' in parent_files:
            for f in files(i + 1)[:255 - n]: move(f, i + 1, i)

def heading(text):
    ''' Display given text as a heading with width scaling with terminal '''
    width = shutil.get_terminal_size((80, 20))[0]
    print(('{:_^' + str(width) + '}\n').format('  ' + text + '  '))

def new_prog_bar(i):
    ''' Initialize a new (dynamically sized) progress bar '''
    global prog, width
    prog = 0
    width = shutil.get_terminal_size((80, 20))[0] - 8 - len(str(i))
    sys.stdout.write(str(i) + 'x255: [' + '.' * width + ']')
    sys.stdout.flush()
    sys.stdout.write('\b' * width)

def update_prog_bar(t):
    ''' Update the current progress bar to reflect progress t (0 < t <= 1) '''
    global prog, width
    if t == 1: sys.stdout.write('#' * (width - prog) + ']\n')
    else:
        d = int(t * width - prog)
        if d > 0:
            sys.stdout.write('#' * d)
            sys.stdout.flush()
            prog += d

def check_synched(compare = True):
    ''' Check that all files in all subdirectories correspond to a matching file in ~/Music '''
    missing, different = [], []

    if compare: heading('Checking files against ~/Music:')
    else: print('Checking files against ~/Music ...')

    # Record files which are missing or differ to ~/Music
    for i, ifiles in file_lists():
        n = len(ifiles)
        if compare: new_prog_bar(i)

        for j in range(n):
            f = ifiles[j]
            if compare: update_prog_bar(float(j) / n)

            if not os.path.isfile(get_path(-1, f)): missing.append((i, f))
            elif compare and not filecmp.cmp(get_path(-1, f), get_path(i, f)):
                different.append((i, f))

        if compare: update_prog_bar(1)

    # Report missing / differing files
    deleted = False
    if len(missing) != 0 or len(different) != 0:
        if compare: print()
        heading('Missing and differing files:')

    for (i, f) in missing:
        print(str(i) + 'x255: No file named "' + f + '" exists in ~/Music')
        if input('Remove file? [y/n]: ') in 'yY':
            os.remove(get_path(i, f))
            deleted = True
            print('File removed.')

    for (i, f) in different:
        print(str(i) + 'x255: File "' + f + '" differs from that in ~/Music')
        if input('Update file? [y/n]: ') in 'yY':
            shutil.copy(get_path(-1, f), get_path(i, f))
            print('File updated.')

    # Redistribute if any files were deleted
    if deleted:
        print()
        redistribute()

moved = {} # Stores final destinations of files moved

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
    for f in files(0):
        if f[-4:] == '.mp3': move(f, 0, 1)

    # Redistribute files & check against ~/Music
    redistribute()
    check_synched(args.compare)

    # Report moved files (only final destination reported)
    if len(moved) != 0: heading('Moved files:')
    for f in moved: print('Moved "' + f + '" into ' + moved[f])

