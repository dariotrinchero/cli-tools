#!/usr/bin/env python3

import argparse
from tkinter import Tk # Read clipboard contents
from urllib.parse import urlparse
from html import unescape
import requests
import re

#---------------------------------------------------------------------------------------------------
# Usage Notes & Examples (run with -h for full help):
#---------------------------------------------------------------------------------------------------
#
# Make shortcut with URL on clipboard and page title for name:
#  $ shortcut.py
#
# Make shortcut with given URL and unmodified page title for name:
#  $ shortcut.py --notrim
#
# Make shortcut with given URL and name:
#  $ shortcut.py --url="https://www.youtube.com/" --name="YouTube"
#


#---------------------------------------------------------------------------------------------------
# NOTE Regex replacements for titles of known sites, comprising pair: (search RE, replacement RE)
#---------------------------------------------------------------------------------------------------
known_sites = {
    'www.youtube.com':      (' - YouTube$', ''),
    'youtu.be':             (' - YouTube$', ''),
    'stackoverflow.com':    ('([a-z]+ - )?(.*?)( - Stack Overflow$)', lambda m: m.group(2)),
    #'en.wikipedia.org':     (' - Wikipedia$', ''),
    'www.reddit.com':       (' : .*?$', '') # remove subreddit name
    }

class Tfmt:
    ''' Formatting codes for terminal output. '''
    WARN = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    HEADER = '\033[95m' + UNDERLINE

def get_clipboard():
    ''' Get current contents of clipboard. '''
    print(f'{Tfmt.WARN}Getting URL from clipboard (use --url to specify URL){Tfmt.ENDC}')
    tk = Tk()
    tk.withdraw()
    clipboard = tk.clipboard_get()
    tk.update()
    tk.destroy()
    return clipboard

def get_site(url):
    ''' Return the site (network location) of given url, after validating URL. '''
    try:
        parsed = urlparse(url)
        assert all([parsed.scheme, parsed.netloc, parsed.path])
    except: exit(f'{Tfmt.FAIL}Invalid URL: "{url}"{Tfmt.ENDC}')
    return parsed.netloc

def get_title(url, site, trim=True):
    ''' Get title of page at given URL, optionally trimming names of known sites. '''
    global known_sites
    print(f'{Tfmt.WARN}Getting name from URL (use --name to specify name){Tfmt.ENDC}')

    try:
        response = requests.get(url=url, timeout=10, headers={'user-agent': 'shortcut/1.0.1'})
        response.raise_for_status()
        match = re.search('<\W*title\W*(.*)\W*</title', response.text, re.IGNORECASE)
        title = unescape(match.group(1))

        # Optionally remove known site names from title
        if site in known_sites and trim:
            print(f'{Tfmt.WARN}Trimming name from known site (use --notrim to avoid){Tfmt.ENDC}')
            r = known_sites[site]
            title = re.sub(r[0], r[1], title)

        return title
    except requests.exceptions.HTTPError:
        errmsg = f'Request to "{url}" returned bad status code {response.status_code}'
    except requests.exceptions.Timeout: errmsg = f'Request to "{url}" timed-out'
    except requests.exceptions.ConnectionError: errmsg = f'Connection error accessing "{url}"'
    except requests.exceptions.TooManyRedirects: errmsg = f'Too many redirects accessing "{url}"'
    except: errmsg = f'Cannot extract title from "{url}"'
    exit(Tfmt.FAIL + errmsg + Tfmt.ENDC)

def sanitize_name(name):
    ''' Sanitise given file name of illegal characters for NTFS or EXT4. '''
    name = re.sub('[<>:"/\|?*]', '', name, flags=re.ASCII)
    return re.sub(' +', ' ', name).strip()[:250] # conservative ext4 max filename length

def make_shortcut(url, name):
    ''' Create new .html file in current directory with given name, linking to given URL. '''
    with open(name + '.html', 'w') as newf:
        newf.write(f'<html><head><meta http-equiv="refresh"content="0;url={url}"/></head></html>')

if __name__ == '__main__':
    # Create argument parser and parse args
    parser = argparse.ArgumentParser(description='Creates HTML page linking to given URL, to act\
            as a cross-platform shortcut. If no name is given, shortcut is named by the title of\
            the page. If destination is in a list of known sites, this title is first trimmed.')
    parser.add_argument('--name', action='store', default=None,
            metavar='NAME', help='name of shortcut (defaults to sanitised page title)')
    parser.add_argument('--url', action='store', default=None,
            metavar='URL', help='URL of shortcut (defaults to clipboard contents)')
    parser.add_argument('--notrim', action='store_false', dest='trim', help='do not trim known\
            web page names from title (if using title for shortcut name)')
    args = parser.parse_args()

    # Create shortcut
    print(f'{Tfmt.HEADER}Creating Shortcut{Tfmt.ENDC}')
    if not args.url: args.url = get_clipboard()
    site = get_site(args.url)
    if not args.name: args.name = get_title(args.url, site, args.trim)
    name = sanitize_name(args.name)
    make_shortcut(args.url, name)

    # Output parameters used
    print(f'\n{Tfmt.BOLD}URL{Tfmt.ENDC}:\t{args.url}')
    print(f'{Tfmt.BOLD}Site{Tfmt.ENDC}:\t{site}')
    print(f'{Tfmt.BOLD}Name{Tfmt.ENDC}:\t{name}')
