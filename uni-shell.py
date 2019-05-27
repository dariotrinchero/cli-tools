#!/usr/bin/env python3

import argparse
import subprocess
import pexpect
import getpass
import sys
from os import environ

#---------------------------------------------------------------------------------------------------
# Usage Notes & Examples (run with -h for full help):
#---------------------------------------------------------------------------------------------------
#
# Connect to NARGA with sftp:
#  $ uni-ssh-ftp.py --reveal
#
# Connect to NARGA with ssh:
#  $ uni-ssh-ftp.py --reveal --ssh
#
#---------------------------------------------------------------------------------------------------
# Dependencies & Manuals:
#---------------------------------------------------------------------------------------------------
#
# FortiClient   https://kb.fortinet.com/kb/documentLink.do?externalID=FD41256
#


def get_password(reveal):
    ''' Read in a password, optionally showing text, and handling exceptions '''
    try:
        if reveal: password = input('Enter password: ')
        else: password = getpass.getpass('Enter password: ')
    except (EOFError, KeyboardInterrupt): password = '' # Ctrl + D / Ctrl + C

    return password

def launch_shell(hostname, password, protocol='sftp'):
    ''' Launches ssh / sftp using given hostname & password. Passes control to user once ssh / sftp
    is active, & waits until it is closed. '''
    shell = pexpect.spawn('{} {}'.format(protocol, hostname))

    if protocol == 'sftp': waitstr = 'sftp> '
    else: waitstr = 'Welcome' # protocol == 'ssh'
    print('Waiting for {}...'.format(protocol))

    try:
        shell.waitnoecho()
        shell.sendline(password)
        shell.expect(waitstr) # Expect this string before giving user control
        print('\n' + waitstr, end='')

        shell.interact()
    except: print('ERROR: Unknown error launching {}; may be timeout.'.format(protocol))

def launch_vpn(vpn_path, server, username, password):
    ''' Launches FortiClient vpn from given path, using given server, username, & password.
    Returns pexpect object running vpn on success & None on error. '''
    try:
        vpn = pexpect.spawn('{} --server {} --vpnuser {}'.format(vpn_path, server, username))

        vpn.waitnoecho()
        vpn.sendline(password)

        print('Waiting for vpn tunnel...')

        while True:
            response = vpn.expect(['(Y/N)', 'Tunnel running', 'tunnel failed', pexpect.EOF,
                pexpect.TIMEOUT])

            if response == 0: vpn.sendline('y') # Respond yes to any prompts
            elif response == 1: return vpn
            elif response <= 3: print('ERROR: Failed to launch tunnel; check password.')
            else: print('ERROR: Timeout while trying to launch tunnel.')

            if response > 1: # If error occurred
                vpn.close()
                return None

    except pexpect.exceptions.ExceptionPexpect:
        print('ERROR: Invalid vpn path.')
        return None
    except:
        print('ERROR: Unknown error launching vpn; may be timeout.')
        return None

if __name__ == '__main__':
    # Get default VPN path
    if 'FORTICLIENT' in environ: vpn_path = environ['FORTICLIENT']
    else: vpn_path = None

    # Create argument parser and parse args
    parser = argparse.ArgumentParser(description='Connects via ssh or sftp to given hostname \
            after launching a FortiClient vpn tunnel to given server and port. This is designed \
            for connecting to Stellenbosch University Ubuntu servers with ssh or sftp.')
    parser.add_argument('--reveal', action='store_true',
            help='show entered password in plaintext (default hides entry)')
    parser.add_argument('--ssh', action='store_const', const='ssh', default='sftp',
            dest='protocol', help='connect using ssh protocol (default connects with sftp)')
    parser.add_argument('--server', action='store', default='fwvpn.sun.ac.za:443',
            help='server and port to connect to with vpn (default "fwvpn.sun.ac.za:443")')
    parser.add_argument('--username', action='store', default='20854714',
            help='username for vpn and ssh / sftp (default "20854714")')
    parser.add_argument('--hostname', action='store', default='open.rga.stb.sun.ac.za',
            help='hostname to connect to with ssh / sftp (default "open.rga.stb.sun.ac.za")')
    parser.add_argument('--vpn-path', action='store', dest='vpn_path', default=vpn_path,
            help='path to FortiClient CLI binary to run (defaults to string contained in \
                    $FORTICLIENT environment variable)')
    args = parser.parse_args()

    if args.vpn_path == None: sys.exit('ERROR: --vpn-path not given and $FORTICLIENT not set.')

    # Execute relevant functions
    password = get_password(args.reveal)
    if password == '': sys.exit(0)

    vpn = launch_vpn(args.vpn_path, args.server, args.username, password)
    if vpn != None:
        launch_shell(args.username + '@' + args.hostname, password, protocol=args.protocol)
        vpn.close()
    else: sys.exit(1)
