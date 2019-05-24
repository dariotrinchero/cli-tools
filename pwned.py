#!/usr/bin/env python3

import hashlib
import requests
import getpass
import argparse

#---------------------------------------------------------------------------------------------------
# Usage Notes & Examples (run with -h for full help):
#---------------------------------------------------------------------------------------------------
#
# Check passwords in loop, showing passwords:
#  $ pwned.py --loop --reveal
#


def get_password(reveal):
    ''' Read in a password, optionally showing text, and handling exceptions '''
    try:
        if reveal: password = input('Enter password: ')
        else: password = getpass.getpass('Enter password: ')
    except (EOFError, KeyboardInterrupt): password = '' # Ctrl + D / Ctrl + C

    pwString = '"%s"' % password if reveal else 'Given password'
    return password, pwString

def get_hashes(password):
    ''' Hash given password and retrieve list of hashes matching prefix of hash '''
    pwHash = hashlib.sha1(password.encode('utf-8')).hexdigest().upper()
    try: hashList = requests.get(url = 'https://api.pwnedpasswords.com/range/{}'.format(pwHash[:5]))
    except:
        print('ERROR: Cannot connect to API.')
        hashList = None
    return pwHash, hashList

def find_matches(hashList, pwHash, pwString):
    ''' Check given list of hashes for exact matches with given hash, and report matches '''
    for match in hashList.text.split('\r\n'):
        split = match.split(':')
        if pwHash[5:] == split[0]:
            occurrences = '{:,d} occurrence{}'.format(int(split[1]), '' if split[1] == '1' else 's')
            print('%s was found\nHash %s\t%s' % (pwString, pwHash, occurrences))
            break
    else: print('%s was not found' % pwString)


if __name__ == '__main__':
    # Create argument parser and parse args
    parser = argparse.ArgumentParser(description='Reads in passwords and checks whether matching \
            passwords have been leaked in data breaches. For this it queries the "Have I Been \
            Pwned" API described at https://haveibeenpwned.com/API/v2#SearchingPwnedPasswordsBy\
            Range and searches through the results for matching passwords.')
    parser.add_argument('--reveal', action='store_true',
            help='show entered password in plaintext (default behaviour hides entry)')
    parser.add_argument('--loop', action='store_true',
            help='keep prompting for passwords until given EOF or empty input (default checks \
                    single password)')
    args = parser.parse_args()

    while True:
        password, pwString = get_password(args.reveal)
        if password == '': break

        pwHash, hashList = get_hashes(password)
        if hashList == None: break

        find_matches(hashList, pwHash, pwString)
        if not args.loop: break
