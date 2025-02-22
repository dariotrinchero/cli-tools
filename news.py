#!/usr/bin/env python3

import argparse
import re
import sys
import textwrap
import subprocess

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

class Term:
    ''' Contains functions to apply ANSI terminal formatting to text. '''

    # ANSI escape sequences
    BOLD  = ('[1m', '[22m')
    DIM   = ('[2m', '[22m')
    ITAL  = ('[3m', '[23m')
    UNDER = ('[4m', '[24m')
    COLOR = ('[38;2;%d;%d;%dm', '[39m')
    LINK  = (']8;;', '\\')

    def __init__(self, width, indent, compact=False, ansi_override=None):
        if ansi_override is not None: self.ansi = ansi_override
        else: self.ansi = sys.stdout.isatty()
        self.width = width
        self.indent = indent
        self.compact = compact

    def fmt(self, text, *styles):
        if not self.ansi: return text
        for style in styles:
            text = '\033' + style[0] + text + '\033' + style[1]
        return text

    def link(self, url, text):
        if not self.ansi: return text
        return self.fmt(url, Term.LINK) + text + self.fmt('', Term.LINK)
    
    def heading(self, text, color=(255, 255, 90)):
        if not self.ansi: return text
        return self.fmt(text, Term.UNDER, Term.BOLD, Term.COLOR) % color

    def wrap(self, line, bullet_lvl=0, compact=False):
        indent_args = {}
        if bullet_lvl > 0:
            newline = not (self.compact or compact)
            indent_args['initial_indent'] = '\n' * newline + ' ' * self.indent * (bullet_lvl - 1) + '\u2022 '
            indent_args['subsequent_indent'] = ' ' * (self.indent * (bullet_lvl - 1) + 2)
        return textwrap.fill(line, self.width, break_long_words=False, **indent_args)

class WikiNews:
    ''' Class for parsing Wikipedia news, as per specification (https://w.wiki/DA2e). '''

    # headline categories
    CATEGORIES = {
        # this list is exhaustive; cf. https://w.wiki/DA2e
        'Arts and culture': '🎨',
        'Armed conflicts and attacks': '⚔️ ',
        'Business and economy': '📈',
        'Disasters and accidents': '🌊',
        'Health and environment': '🌱',
        'International relations': '🌐',
        'Law and crime': '⚖️ ',
        'Politics and elections': '🗳 ',
        'Science and technology': '🧬',
        'Sports': '🏀'
    }

    def __init__(self, day):
        # retrieve current events for given day from Wikipedia as list of lines
        day_str = day.strftime('%Y_%B_%d')
        try:
            req = urlopen(f'https://en.wikipedia.org/wiki/Portal:Current_events/{day_str}?action=raw')
        except (HTTPError, URLError) as err:
            exit(f'Unable to retrieve news for {day.strftime("%-d %B %Y")}: {err}')
        self.news = bytes(req.read()).decode('utf-8').split('\n')[3:-1] # trim non-content

    def __iter__(self):
        return map(WikiNews.add_heading_icon, iter(self.news))

    def is_blank(self):
        return self.news == ['*'] # defaul empty template

    def bullet_lvl(self, line):
        ''' Get indent level of bullet on given line (0 if out of range, or not list item). '''
        if not 0 < line < len(self.news): return 0
        bullets = re.search('^\*+(?=[^*])', self.news[line])
        return 0 if not bullets else bullets.end()
    
    @staticmethod
    def add_heading_icon(line):
        ''' TODO '''
        m = re.match("^'''(.+)'''$", line)
        if m is None: return line
        key = m.group(1).capitalize()
        return line if key not in WikiNews.CATEGORIES else f"'''{WikiNews.CATEGORIES[key]} {key}'''"

    @staticmethod
    def __valid_url(url):
        ''' Return whether given string is a well-formed URL. '''
        try:
            parsed = urlparse(url)
            return all([parsed.scheme, parsed.netloc])
        except AttributeError:
            return False

    @staticmethod
    def parse_link(dest, text=None):
        ''' Get hyperlink URL & display test for given destination, possibly a wikilink. '''
        if not WikiNews.__valid_url(dest): # convert internal wikilink to URL
            if text is None: text = dest
            target = quote(dest.replace(' ', '_'))
            dest = f'https://en.wikipedia.org/wiki/{target}'
        return (dest, dest if text is None else text)

def print_news(day, term):
    ''' Prints news headlines for given day from Wikipedia, formatted for terminal output. '''
    # print date heading & loading filler text
    print(term.heading(day.strftime('%A, %-d %B %Y')) + '\n\n'
        + term.fmt('fetching...\r', Term.DIM, Term.ITAL) * term.ansi, end='')
    sys.stdout.flush()

    # fetch headlines & create Wiki news parser
    news = WikiNews(day)
    if news.is_blank():
        print(term.fmt("(no news)  ", Term.ITAL))
        return

    # format & output headlines
    for l, line in enumerate(news):
        # conceal link URLs for better line wrapping
        link_urls = []
        def hide_link(m, delim):
            url, text = WikiNews.parse_link(m.group(1), m.group(3))
            link_urls.insert(0, url)
            return delim + text.replace(' ', '\u00A0') + delim # non-breaking space

        line = re.sub(r'\[\[(.+?)(\|(.+?))?\]\]', lambda m: hide_link(m, '\u00b6'), line)
        line = re.sub(r'\[([^ ]+)( (\(.+?\)))?\]', lambda m: hide_link(m, '\u204b'), line)

        # bullet points & line wrapping
        lvl = news.bullet_lvl(l)
        compact = 0 < news.bullet_lvl(l - 1) < lvl < news.bullet_lvl(l + 1)
        line = term.wrap(line[lvl:].strip(), lvl, compact)

        # bold & italics
        line = re.sub("'''(.+?)'''", lambda m: term.fmt(m.group(1), Term.BOLD), line)
        line = re.sub("''(.+?)''", lambda m: term.fmt(m.group(1), Term.ITAL), line)

        # restore link destinations
        show_link = lambda m: term.link(link_urls.pop(), m.group(1))
        line = re.sub('\u00b6(.+?)\u00b6', show_link, line)
        line = re.sub('\u204b(.+?)\u204b', show_link, line)

        print(line)

if __name__ == '__main__':
    # create argument parser & parse args
    parser = argparse.ArgumentParser(description="Retrieves & displays recent news headlines, as\
        retrieved from the Wikipedia 'Portal:Current events' page. Headlines are rendered as bullet\
        points, grouped by category, with hyperlinks to related pages on Wikipedia, as well as news\
        sources.")
    parser.add_argument('days', nargs='?', type=int, default=None, help='number of days (including\
        today) to retrieve (defaults to 2, or 7 if --less is given)')
    parser.add_argument('--width', type=int, default=None, help='maximum output width')
    parser.add_argument('--indent', type=int, default=3, help='width of indent for nested lists')
    parser.add_argument('--compact', action='store_true', help='reduce blank lines between bullets')
    parser.add_argument('--ansi', choices=['y', 'n', 'auto'], default='auto',
        help='whether to apply ANSI styling to output')
    parser.add_argument('--less', action='store_true', help='automatically pipe output through less\
        to get scrolling newsfeed')
    args = parser.parse_args()

    # create terminal formatter
    width = args.width or min(int(0.8 * get_terminal_size((120, 24)).columns), 120)
    term = Term(width, args.indent, args.compact, None if args.ansi == 'auto' else args.ansi == 'y')

    if args.less:
        # pipe stdout to stdin of 'less' subprocess
        less_proc = subprocess.Popen(['less', '-c', '-r'], stdin=subprocess.PIPE, text=True)
        sys.stdout = less_proc.stdin

    # output news for each day
    today = date.today()
    for d in range(args.days or 7 if args.less else 2):
        if d > 0: print() # separating line
        print_news(today - timedelta(days=d), term)

    if args.less:
        less_proc.stdin.close()
        less_proc.wait()
