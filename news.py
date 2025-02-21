#!/usr/bin/env python3

import argparse
import re
import sys
import textwrap
import subprocess

from math import nan, isnan
from datetime import date, timedelta
from shutil import get_terminal_size

from urllib.request import urlopen
from urllib.parse import urlparse, quote
from urllib.error import HTTPError, URLError

#---------------------------------------------------------------------------------------------------
# Usage Notes & Examples (run with -h for full help):
#---------------------------------------------------------------------------------------------------
#
# Retrieve news of last two days (including today):
#  $ news.py 2
#
# Limit output width & set indent size for nested lists:
#  $ news.py 2 --width=150 --indent=3
#
# Retrieve news of entire week in scrolling feed:
#  $ news.py --less
#
#---------------------------------------------------------------------------------------------------

def human_date(day):
    ''' Convert date to string in verbose human-readable format. '''
    return day.strftime('%A, %-d %B %Y')

def get_news(day):
    ''' Retrieve current events for given day from Wikipedia; return list of lines. '''
    day_str = day.strftime('%Y_%B_%d')
    try:
        req = urlopen(f'https://en.wikipedia.org/wiki/Portal:Current_events/{day_str}?action=raw')
    except (HTTPError, URLError) as err:
        exit(f'Unable to retrieve news for {human_date(day)}: {err}')
    return bytes(req.read()).decode('utf-8').split('\n')[3:-1] # head & tail aren't content

def valid_url(url):
    ''' Return whether given string is a well-formed URL. '''
    try:
        parsed = urlparse(url)
        return all([parsed.scheme, parsed.netloc])
    except AttributeError:
        return False

def parse_link(dest, text=None):
    ''' Get hyperlink URL & display test for given destination, possibly a wikilink. '''
    if not valid_url(dest): # convert internal wikilink to URL
        if text is None: text = dest
        target = quote(dest.replace(' ', '_'))
        dest = f'https://en.wikipedia.org/wiki/{target}'
    return (dest, dest if text is None else text)

class Term:
    ''' Contains functions to apply ANSI terminal formatting to text. '''
    ANSI_SUPPORTED = True

    # ANSI escape sequences
    BOLD  = ('[1m', '[22m')
    DIM   = ('[2m', '[22m')
    ITAL  = ('[3m', '[23m')
    UNDER = ('[4m', '[24m')
    COLOR = ('[38;2;%d;%d;%dm', '[39m')
    LINK  = (']8;;', '\\')

    def fmt(text, *styles):
        if not Term.ANSI_SUPPORTED: return text
        for style in styles:
            text = '\033' + style[0] + text + '\033' + style[1]
        return text

    def link(url, text):
        if not Term.ANSI_SUPPORTED: return text
        return Term.fmt(url, Term.LINK) + text + Term.fmt('', Term.LINK)
    
    def heading(text, color=(255, 255, 90)):
        if not Term.ANSI_SUPPORTED: return text
        return Term.fmt(text, Term.UNDER, Term.BOLD, Term.COLOR) % color

def bullet_lvl(lines, line):
    ''' Get indent level of bullet on given line (NaN if line out of range, or not bullet list item). '''
    if not 0 < line < len(lines): return nan
    bullets = re.search('^\*+(?=[^*])', lines[line])
    return nan if not bullets else bullets.end()
    
def print_news(day, width=100, indent=3, compact=False):
    ''' Prints news headlines for given day from Wikipedia, formatted for terminal output. '''
    # print date heading & loading filler text
    print(Term.heading(human_date(day)) + '\n\n'
        + Term.fmt('fetching...\r', Term.DIM, Term.ITAL) * Term.ANSI_SUPPORTED, end='')
    sys.stdout.flush()

    # fetch headlines
    news = get_news(day)
    for l, line in enumerate(news):
        # conceal link URLs for better line wrapping
        link_urls = []
        def hide_link(m, delim):
            url, text = parse_link(m.group(1), m.group(3))
            link_urls.insert(0, url)
            return delim + text.replace(' ', '\u00A0') + delim # non-breaking space

        line = re.sub(r'\[\[(.+?)(\|(.+?))?\]\]', lambda m: hide_link(m, '\u00b6'), line)
        line = re.sub(r'\[([^ ]+)( (\(.+?\)))?\]', lambda m: hide_link(m, '\u204b'), line)

        # bullet points & line wrapping
        indent_args = {}
        lvl = bullet_lvl(news, l)
        if not isnan(lvl):
            line = line[lvl:].strip()
            dense = compact or bullet_lvl(news, l - 1) < lvl < bullet_lvl(news, l + 1)
            indent_args['initial_indent'] = '\n' * (not dense) + ' ' * indent * (lvl - 1) + '\u2022 '
            indent_args['subsequent_indent'] = ' ' * (indent * (lvl - 1) + 2)
        line = textwrap.fill(line, width, break_long_words=False, **indent_args)

        # bold & italics
        line = re.sub("'''(.+?)'''", lambda m: Term.fmt(m.group(1), Term.BOLD), line)
        line = re.sub("''(.+?)''", lambda m: Term.fmt(m.group(1), Term.ITAL), line)

        # restore link destinations
        show_link = lambda m: Term.link(link_urls.pop(), m.group(1))
        line = re.sub('\u00b6(.+?)\u00b6', show_link, line)
        line = re.sub('\u204b(.+?)\u204b', show_link, line)

        print(line)

if __name__ == '__main__':
    # base default width on terminal size
    width = min(int(0.8 * get_terminal_size((120, 24)).columns), 120)

    # create argument parser & parse args
    parser = argparse.ArgumentParser(description="Retrieves & displays recent news headlines, as\
        retrieved from the Wikipedia 'Portal:Current events' page. Headlines are rendered as bullet\
        points, grouped by category, with hyperlinks to related pages on Wikipedia, as well as news\
        sources.")
    parser.add_argument('days', nargs='?', type=int, default=None, help='number of days (including\
        today) to retrieve (defaults to 2, or 7 if --less is given)')
    parser.add_argument('--width', type=int, default=width, help='maximum output width')
    parser.add_argument('--indent', type=int, default=3, help='width of indent for nested lists')
    parser.add_argument('--compact', action='store_true', help='reduce blank lines between bullets')
    parser.add_argument('--ansi', choices=['y', 'n', 'auto'], default='auto',
        help='whether to apply ANSI styling to output')
    parser.add_argument('--less', action='store_true', help='automatically pipe output through less\
        to get scrolling newsfeed')
    args = parser.parse_args()

    # check whether to apply ansi styling
    Term.ANSI_SUPPORTED = args.ansi == 'y' or args.ansi == 'auto' and sys.stdout.isatty()

    if args.less:
        # pipe stdout to stdin of 'less' subprocess
        less_proc = subprocess.Popen(['less', '-c', '-r'], stdin=subprocess.PIPE, text=True)
        sys.stdout = less_proc.stdin

    # output news for each day
    today = date.today()
    for d in range(args.days or 7 if args.less else 2):
        if d > 0: print()
        print_news(today - timedelta(days=d), args.width, args.indent, args.compact)

    if args.less:
        less_proc.stdin.close()
        less_proc.wait()
