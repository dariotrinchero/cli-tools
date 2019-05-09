#!/usr/bin/env python3

import os
import shutil
import sys
import filecmp
from itertools import count

#---------------------------------------------------------------------------------------------------
# Usage:
#---------------------------------------------------------------------------------------------------
#
# $ python3 distribute.py [flags]
#
#---------------------------------------------------------------------------------------------------
# Functionality:
#---------------------------------------------------------------------------------------------------
#
# Note: Herein "parent" is used to refer to the directory containing this script.
#
# Sorts all .mp3 files in the parent directory alphabetically into sub-directories named
# "1x255", "2x255", ..., each containing 255 files.
#
# Logs the destinations of all files moved, and warns the user if any of the files in either the
# parent directory or any of the sub-directories do not exist (with the same name) in ~/Music, or
# if the equivalent file in ~/Music differs from the file in question.
#
# The purpose of this script is to divide music on a USB flash drive into directories of no more
# than 255 files each, as required by the sound system of the Toyota Yarris, in a way which is
# logical and systematic. In addition, the script notifies the user if the music on the USB drive
# is out-of-sync with the main music library in ~/Music (such as if a song is cropped, or tags are
# updated).
#
#---------------------------------------------------------------------------------------------------
# Limitations:
#---------------------------------------------------------------------------------------------------
#
# The script assumes that files already within the sub-directories of the parent directory ("1x255",
# "2x255", ...) are sorted in alphabetical order. Thus, when redistributing, only the first or last
# few files in each folder are ever moved. Thus, the script will not detect if, say, all folders
# contain the correct number of files, but one file is in the wrong folder.
#
#---------------------------------------------------------------------------------------------------
# Command line arguments:
#---------------------------------------------------------------------------------------------------
#
# nocompare	do not compare contents of files to those in ~/Music
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
    elif compare and not filecmp.cmp(get_path(-1, f), get_path(i, f)):
        print('File "' + f + '" differs from that in ~/Music')
        if input('Update file? [y/n]: ') in 'yY':
            shutil.copy(get_path(-1, f), get_path(i, f))
            print('File updated.')

moved = {} # Stores final destinations of all files moved
parent_files = files(0)
args = sys.argv

# Move all .mp3 files from parent into 1x255
for f in parent_files:
    if f[-4:] == '.mp3': move(f, 0, 1)

# Redistribute files and check against ~/Music
for i in count(1):
    if str(i) + 'x255' in parent_files:
        ifiles = files(i)
        for f in ifiles: check_synched(f, i, not 'nocompare' in args)

        n = len(ifiles)
        if n > 255:
            for f in ifiles[255:]: move(f, i, i + 1)
        elif n < 255 and str(i + 1) + 'x255' in parent_files:
            for f in files(i + 1)[:255 - n]: move(f, i + 1, i)
    else: break

# Report changes (a single file moved many times is reported only once)
for f in moved: print('Moved "' + f + '" into ' + moved[f])

