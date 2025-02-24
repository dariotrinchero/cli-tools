#!/usr/bin/env python3

import argparse
import re
import subprocess
import sys
import textwrap

from datetime import date, timedelta
from shutil import get_terminal_size

from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

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

    # ANSI styles (& their escape sequences)
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

    # shorthand aliases
    def bold(self, text): return self.fmt(text, Term.BOLD)
    def dim(self, text): return self.fmt(text, Term.DIM)
    def ital(self, text): return self.fmt(text, Term.ITAL)
    def under(self, text): return self.fmt(text, Term.UNDER)

    def color(self, text, color):
        return self.fmt(text, (Term.COLOR[0] % color, Term.COLOR[1]))

    def link(self, url, text):
        if not self.ansi: return text
        return self.fmt(url, Term.LINK) + text + self.fmt('', Term.LINK)
    
    def heading(self, text, color=(255, 255, 90)):
        return self.color(self.fmt(text, Term.UNDER, Term.BOLD), color)

    def newline(self):
        return '\n' * (not self.compact)

    def backspace(self, chrs=1):
        return ('\r' + ' ' * chrs + '\r') * self.ansi

    def wrap(self, line, list_lvl=0):
        indent_args = {}
        if list_lvl > 0:
            indent_args['initial_indent'] = ' ' * self.indent * (list_lvl - 1) + '\u2022 '
            indent_args['subsequent_indent'] = ' ' * (self.indent * (list_lvl - 1) + 2)
        return textwrap.fill(line, self.width, break_long_words=False, **indent_args)


class WikiNews:
    ''' Class for parsing Wikipedia news, as per specification (https://w.wiki/DA2e). '''

    # headline categories
    CATEGORIES = { # this list is exhaustive; cf. https://w.wiki/DA2e
        'Arts and culture':            '🎨',
        'Armed conflicts and attacks': '⚔️ ',
        'Business and economy':        '📈',
        'Disasters and accidents':     '🌊',
        'Health and environment':      '🌱',
        'International relations':     '🌐',
        'Law and crime':               '⚖️ ',
        'Politics and elections':      '🗳 ',
        'Science and technology':      '🧬',
        'Sports':                      '🏀'
    }

    # base URL of English Wikipedia
    URL = 'https://en.wikipedia.org/wiki/'

    # unit separator; delimits links with hidden URLs
    DELIM = '\x1F'

    # fragments of regex patterns
    HYPERLINK   = r'\[(?P<url>[^ []+)( (?P<htxt>.+?))?\]'
    WIKILINK    = r'\[\[(?P<dest>.+?)(\|(?P<wtxt>.+?))?\]\](?P<suffix>[a-zA-Z]*)'

    # compiled regex patterns
    BOLD =       re.compile("'''(?P<txt>.+?)'''")
    BRACKET =    re.compile(r'\((?P<txt>.+?)\)')
    BULLET =     re.compile(r'^\*+(?=[^*])')
    HEADING =    re.compile("^'''(?P<txt>.+)'''$")
    HIDDENLINK = re.compile(f'{DELIM}(?P<txt>.+?){DELIM}')
    ITAL =       re.compile("''(?P<txt>.+?)''")
    LINK =       re.compile(f'(?P<wiki>{WIKILINK})|(?P<hyper>{HYPERLINK})')

    def __init__(self, day, heading_icons=True):
        self.heading_icons = heading_icons

        # retrieve news for given day from Wikipedia as list of lines
        day_str = day.strftime('%Y_%B_%d')
        try:
            with urlopen(f'{WikiNews.URL}Portal:Current_events/{day_str}?action=raw') as req:
                # decode & trim non-content
                self.news = bytes(req.read()).decode('utf-8').split('\n')[3:-1]
        except (HTTPError, URLError) as err:
            exit(f'Unable to retrieve news for {day.strftime("%-d %B %Y")}: {err}')

    def format(self, formatter):
        ''' Yield news lines, formatted using given formatter. '''
        if self.news == ['*']: # default empty template
            yield formatter.ital('(no news)')
            return

        for l, line in enumerate(self.news):
            if not line.strip(): continue

            # conceal link URLs for better line wrapping
            urls = []
            line = WikiNews.LINK.sub(lambda m: WikiNews.__parse_link(m, urls), line)

            # bullet points & line wrapping
            lvl = self.__list_lvl(l)
            line = formatter.wrap(line[lvl:].strip(' '), lvl)

            # bold, italics & headings
            line = self.__format_heading(line, formatter)
            line = WikiNews.BOLD.sub(lambda m: formatter.bold(m.group('txt')), line)
            line = WikiNews.ITAL.sub(lambda m: formatter.ital(m.group('txt')), line)

            # add spacing if level is not between levels of adjacent lines
            if lvl > 0 and not (0 < self.__list_lvl(l - 1) < lvl < self.__list_lvl(l + 1)):
                line = formatter.newline() + line

            # restore link URLs
            line = WikiNews.HIDDENLINK.sub(lambda m:
                formatter.link(urls.pop(), m.group('txt')), line)

            yield line

    def __list_lvl(self, line):
        ''' Get indent level of bullet on given line (0 if out of range, or not list item). '''
        if not 0 < line < len(self.news): return 0
        bullets = WikiNews.BULLET.search(self.news[line])
        return 0 if not bullets else bullets.end()

    def __format_heading(self, line, formatter, color=(255, 255, 170)):
        ''' Apply appropriate formatting to line if heading; else leave unchanged. '''
        m = WikiNews.HEADING.match(line)
        if m is None: return line
        heading = m.group('txt').capitalize()
        if heading not in WikiNews.CATEGORIES: return line

        icon = WikiNews.CATEGORIES[heading] + ' ' if self.heading_icons else ''
        return '\n' + icon + formatter.bold(formatter.color(heading, color))

    @staticmethod
    def __parse_link(m, urls):
        ''' Take regex match for link, & list of URLs; append URL to list, & return link text. '''
        if m.group('wiki'): # link is Wikilink
            url, text = m.group('dest'), m.group('wtxt') or m.group('dest')
            text += m.group('suffix') or ''

            # obtain full URL for Wikilink
            target = quote(url.replace(' ', '_'))
            url = f'{WikiNews.URL}{target}'

        else: # link is external
            url, text = m.group('url'), m.group('htxt') or m.group('url')

            # (consistently) bracket & italicise link text
            text = WikiNews.BRACKET.sub(lambda m: m.group('txt'), text)
            text = WikiNews.ITAL.sub(lambda m: m.group('txt'), text)
            text = "(''" + text + "'')"

        # wrap display text in delimiters & make spaces non-breaking
        text = WikiNews.DELIM + text.replace(' ', '\u00A0') + WikiNews.DELIM

        # record hidden URL in list & return display text
        urls.insert(0, url)
        return text


def print_news(days, term, heading_icons=True):
    ''' Print news headlines from Wikipedia for given number of days. '''
    today = date.today()
    for d in range(days):
        day = today - timedelta(days=d)

        print(term.heading(day.strftime('%A, %-d %B %Y')))
        print(term.fmt('\nfetching...', Term.DIM, Term.ITAL) * term.ansi, end='')
        sys.stdout.flush()

        news = '\n'.join(WikiNews(day, heading_icons).format(term))
        print(term.backspace(11) + news.strip() + '\n')

if __name__ == '__main__':
    # create argument parser & parse args
    parser = argparse.ArgumentParser(description="Retrieves & displays recent news headlines, as\
        retrieved from the Wikipedia 'Portal:Current events' page. Headlines are rendered as bullet\
        points, grouped by category, with hyperlinks to related pages on Wikipedia, as well as news\
        sources.")
    parser.add_argument('days', nargs='?', type=int, default=None, help='number of days (including\
        today) to retrieve (defaults to 2, or 5 if --less is given)')
    parser.add_argument('--width', type=int, default=None, help='maximum output width')
    parser.add_argument('--indent', type=int, default=3, help='width of indent for nested lists')
    parser.add_argument('--compact', action='store_true', help='reduce blank lines between bullets')
    parser.add_argument('--ansi', choices=['y', 'n', 'auto'], default='auto',
        help='whether to apply ANSI styling to output')
    parser.add_argument('--less', action='store_true', help='automatically pipe output through less\
        to get scrolling newsfeed')
    parser.add_argument('--no-icons', action='store_false', dest='icons', help='suppress emoji in\
        headline category headings')
    args = parser.parse_args()

    # create terminal formatter
    width = args.width or min(int(0.8 * get_terminal_size((120, 24)).columns), 120)
    term = Term(width, args.indent, args.compact, None if args.ansi == 'auto' else args.ansi == 'y')

    try:
        if args.less:
            # attach stdout to stdin of 'less' subprocess
            less_proc = subprocess.Popen(['less', '-crK'], stdin=subprocess.PIPE, text=True)
            sys.stdout = less_proc.stdin

        # print news headlines
        print_news(args.days or (5 if args.less else 2), term, args.icons)

    except (BrokenPipeError, KeyboardInterrupt): pass # ignore user exiting early

    finally:
        if args.less:
            less_proc.stdin.close()
            less_proc.wait()
