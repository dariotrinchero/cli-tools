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

    pw_str = '"%s"' % password if reveal else 'Given password'
    return password, pw_str

def get_hashes(password):
    ''' Hash given password and retrieve list of hashes matching prefix of hash '''
    pw_hash = hashlib.sha1(password.encode('utf-8')).hexdigest().upper()
    try: hash_list = requests.get(url='https://api.pwnedpasswords.com/range/{}'.format(pw_hash[:5]),
                headers={'Add-Padding': 'true'})
    except: hash_list = None
    return pw_hash, hash_list

def find_matches(hash_list, pw_hash, pw_str):
    ''' Check given list of hashes for exact matches with given hash, and report matches '''
    for match in hash_list.text.split('\r\n'):
        split = match.split(':')
        occurences = int(split[1])
        if occurences > 0 and pw_hash[5:] == split[0]:
            print('{} was found\nHash {}\t{:,d} occurence{}'.format(pw_str, pw_hash, occurences,
                (occurences > 1) * 's'))
            break
    else: print('%s was not found' % pw_str)


if __name__ == '__main__':
    # Create argument parser and parse args
    parser = argparse.ArgumentParser(description='Reads in passwords and checks whether matching\
            passwords have been leaked in data breaches. For this it queries the "Have I Been\
            Pwned" API detailed at https://haveibeenpwned.com/API/v2#SearchingPwnedPasswordsByRange\
            and searches through the results for matching passwords.')
    parser.add_argument('--reveal', action='store_true',
            help='show entered password in plaintext (default behaviour hides entry)')
    parser.add_argument('--loop', action='store_true',
            help='keep prompting for passwords until given EOF or empty input (default checks\
                    single password)')
    args = parser.parse_args()

    while True:
        password, pw_str = get_password(args.reveal)
        if password == '': break

        pw_hash, hash_list = get_hashes(password)
        if hash_list == None: exit('ERROR: Cannot connect to API')

        find_matches(hash_list, pw_hash, pw_str)
        if not args.loop: break
