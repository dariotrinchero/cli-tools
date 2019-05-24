#!/usr/bin/env python3

import argparse
import sys
from tkinter import Tk # Read clipboard contents
from urllib.parse import urlparse # Validate URL
import requests # Get HTML and extract <title>
import re
from html import unescape # Expand HTML character entities

#---------------------------------------------------------------------------------------------------
# Usage Notes & Examples (run with -h for full help):
#---------------------------------------------------------------------------------------------------
#
# Make shortcut with URL on clipboard and page title for name:
#  $ make-shortcut.py
#
# Make shortcut with given URL and unmodified page title for name:
#  $ make-shortcut.py --notrim
#
# Make shortcut with given URL and name:
#  $ make-shortcut.py --url="https://www.youtube.com/" --name="YouTube"
#


#---------------------------------------------------------------------------------------------------
# NOTE Regex replacements for titles of known sites, consisting of pair: (search RE, replacement RE)
#---------------------------------------------------------------------------------------------------
known_sites = {
    'www.youtube.com':      (' - YouTube$', ''),
    'stackoverflow.com':    ('([a-z]+ - )?(.*?)( - Stack Overflow$)', lambda m: m.group(2)),
    'en.wikipedia.org':     (' - Wikipedia$', ''),
    'www.reddit.com':       (' : .*?$', '') # Remove subreddit name
    }

def get_clipboard():
    ''' Get current contents of clipboard. '''
    print('Getting URL from clipboard (use --url to specify URL)')
    tk = Tk()
    tk.withdraw()
    clipboard = tk.clipboard_get()
    tk.update()
    tk.destroy()
    return clipboard

def get_site(url):
    ''' Return the site (network location) of given url, after sanitising URL. '''
    try:
        parsed = urlparse(url)
        valid = all([parsed.scheme, parsed.netloc, parsed.path])
    except: valid = False

    if not valid:
        print('ERROR: "{}" is an invalid URL.'.format(url))
        sys.exit(1)

    return parsed.netloc

def get_name(url, site=None, trim=True):
    ''' Get title of page at given URL, and optionally trim titles from known sites. '''
    print('Getting name from URL (use --name to specify name)')
    try:
        response = requests.get(url=url, timeout=10, headers={'user-agent': 'make-shortcut/0.0.1'})
        response.raise_for_status()
        t = response.text
        name = t[t.find('<title>') + 7:t.find('</title>')]

        return format_name(name, site, trim)
    except requests.exceptions.HTTPError:
        print('ERROR: Request to URL "{}" returned bad status code {}.'.format(url,
            response.status_code))
        sys.exit(1)
    except requests.exceptions.Timeout:
        print('ERROR: Request to URL "{}" timed-out.'.format(url))
        sys.exit(1)
    except ConnectionError:
        print('ERROR: Connection error while accessing URL "{}".'.format(url))
        sys.exit(1)
    except requests.exceptions.TooManyRedirects:
        print('ERROR: Too many redirects while accessing URL "{}".'.format(url))
        sys.exit(1)

def format_name(name, site=None, trim=True):
    ''' Sanitise given Unix file name, and optionally trim names from known sites. '''
    global known_sites
    if site in known_sites and trim:
        print('Trimming name from known site (use --notrim to avoid)')
        r = known_sites[site]
        name = re.sub(r[0], r[1], name)
    name = re.sub('[^-.() \w]', '', unescape(name), flags=re.ASCII)
    return re.sub(' +', ' ', name).strip()

def make_desktop(url, name):
    ''' Create new .desktop file in current directory with given name, linking to given URL. '''
    with open(name + '.desktop', 'w') as newf:
        newf.write(('[Desktop Entry]\nEncoding=UTF-8\nName={0:}\nType=Link\nURL={1:}\n' +
            'Icon=firefox\nName[en-ZA]={0:}').format(name, url))

if __name__ == '__main__':
    # Create argument parser and parse args
    parser = argparse.ArgumentParser(description='Creates .desktop shortcut for given URL and \
            shortcut name. If no name is given, shortcut is named by the title of the page. If \
            the website is in a list of known sites, this title is first trimmed.')
    parser.add_argument('--name', action='store', default=None,
            metavar='NAME', help='name of shortcut (defaults to sanitised page title)')
    parser.add_argument('--url', action='store', default=None,
            metavar='URL', help='URL of shortcut (defaults to clipboard contents)')
    parser.add_argument('--notrim', action='store_false', dest='trim', help='do not trim title of \
            known webpage (if title is used for name)')
    args = parser.parse_args()

    # Execute relevant functions
    print('[Creating shortcut]\n')
    if not args.url: args.url = get_clipboard()
    site = get_site(args.url)
    if not args.name: args.name = get_name(args.url, site, args.trim)

    print('URL:\t{}\nSite:\t{}\nName:\t{}'.format(args.url, site, args.name))
    make_desktop(args.url, args.name)
