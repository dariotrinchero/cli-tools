#!/usr/bin/env python3

import argparse
import asyncio
import re
import subprocess
import sys
import textwrap

from datetime import date, timedelta
from html.parser import HTMLParser
from json import loads as json
from shutil import get_terminal_size as term_size

from urllib.error import HTTPError, URLError
from urllib.parse import quote as escape
from urllib.request import urlopen, Request

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


#---------------------------------------------------------------------------------------------------
# Terminal output formatter
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

#---------------------------------------------------------------------------------------------------
# Wikipedia news fetcher & parser
#---------------------------------------------------------------------------------------------------

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

    # Wikipedia URLs
    BASEURL   = 'https://en.wikipedia.org/'
    WIKIURL   = f'{BASEURL}wiki/'
    NEWSURL   = f'{WIKIURL}Portal:Current_events/%s?action=raw'
    APIURL    = f'{BASEURL}w/api.php'
    EXPANDURL = f'{APIURL}?action=expandtemplates&prop=wikitext&format=json&text=%s'

    # urlopen wrapper with spoofed User-Agent header
    HEADERS={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0'}
    FETCH = lambda url: urlopen(Request(url, headers=WikiNews.HEADERS))

    # unit separator; delimits links with hidden URLs
    DELIM = '\x1F'

    # fragments of regex patterns
    HYPERLINK   = r'\[(?P<url>[^ []+)( (?P<htxt>.+?))?\]'
    WIKILINK    = r'\[\[(?P<dest>[^[]+?)(\| *(?P<wtxt>.+?))?\]\](?P<suffix>[a-zA-Z]*)'

    # compiled regex patterns
    BOLD =       re.compile("'''(?P<txt>.+?)'''", flags=re.DOTALL)
    BRACKET =    re.compile(r'\((?P<txt>.+?)\)', flags=re.DOTALL)
    BULLET =     re.compile(r'^\*+')
    HEADING =    re.compile("^'''(?P<txt>.+)'''$")
    HIDDENLINK = re.compile(f'{DELIM}(?P<txt>.+?){DELIM}', flags=re.DOTALL)
    ITAL =       re.compile("''(?P<txt>.+?)''", flags=re.DOTALL)
    LINK =       re.compile(f'(?P<wiki>{WIKILINK})|(?P<hyper>{HYPERLINK})')
    TEMPLATE =   re.compile(r'\{\{(?P<txt>.+?)\}\}')

    def __init__(self, day, heading_icons=True):
        self.heading_icons = heading_icons
        self.icons = { WikiNews.__cat_key(k): v for (k, v) in WikiNews.CATEGORIES.items() }

        # retrieve news for given day from Wikipedia as list of lines
        day_str = day.strftime('%Y_%B_%-d')
        try:
            with WikiNews.FETCH(WikiNews.NEWSURL % day_str) as req:
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

            # expand templates
            line = WikiNews.TEMPLATE.sub(lambda m: WikiNews.__expand_template(m.group(0)), line)

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
        if not 0 <= line < len(self.news): return 0
        bullets = WikiNews.BULLET.search(self.news[line])
        return 0 if not bullets else bullets.end()

    def __format_heading(self, line, formatter, color=(255, 255, 170)):
        ''' Apply appropriate formatting to line if heading; else leave unchanged. '''
        try:
            heading = WikiNews.HEADING.match(line).group('txt').capitalize()
            icon = (self.icons[WikiNews.__cat_key(heading)] + ' ') * self.heading_icons
            return '\n' + icon + formatter.bold(formatter.color(heading, color))
        except: # line isn't heading (regex didn't match / dictionary lookup failed)
            return line

    @staticmethod
    def __cat_key(string):
        ''' Contributors may err in category names; derive key to ignore minor variations. '''
        words = string.lower().split()
        return words[0][:3] + str(len(words)) + str('and' in words)

    @staticmethod
    def __parse_link(m, urls):
        ''' Take regex match for link, & list of URLs; append URL to list, & return link text. '''
        if m.group('wiki'): # link is Wikilink
            url, text = m.group('dest'), m.group('wtxt') or m.group('dest')
            text += m.group('suffix') or ''

            # obtain full URL for Wikilink
            url = WikiNews.WIKIURL + escape(url.replace(' ', '_'))

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

    @staticmethod
    def __strip_html(text):
        ''' Strip HTML tags from given text. '''
        data = []
        strip_html = HTMLParser()
        strip_html.handle_data = data.append
        strip_html.feed(text)
        return ''.join(data)

    @staticmethod
    def __expand_template(template):
        ''' Expand given template using Wikipedia API; strip output of HTML tags. '''
        try:
            with WikiNews.FETCH(WikiNews.EXPANDURL % escape(template)) as req:
                response = json(bytes(req.read()).decode('utf-8'))
                return WikiNews.__strip_html(response['expandtemplates']['wikitext'])
        except: return template # fall back to returning input unaltered

#---------------------------------------------------------------------------------------------------
# Global functions for printing news in parallel
#---------------------------------------------------------------------------------------------------

async def get_news(day, heading_icons):
    ''' Asynchronously retrieve news headlines from Wikipedia for given day. '''
    return '\n'.join(WikiNews(day, heading_icons).format(term)).strip() + '\n'

async def print_news(days, term, heading_icons=True, less=False):
    ''' Print news headlines from Wikipedia for given number of days. '''
    today = date.today()
    day_range = [today - timedelta(days=d) for d in range(days)]

    # queue news fetch requests for all days
    news = [asyncio.create_task(get_news(day, heading_icons)) for day in day_range]

    # print results upon completion
    loadmsg = term.ansi and not less
    for d, day in enumerate(day_range):
        print(term.heading(day.strftime('%A, %-d %B %Y')) + '\n')
        print(term.fmt('fetching...', Term.DIM, Term.ITAL) * loadmsg, end='', flush=True)
        print(term.backspace(11) * loadmsg + await news[d])

#---------------------------------------------------------------------------------------------------
# Main routine for parsing arguments
#---------------------------------------------------------------------------------------------------

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
    width = args.width or min(int(0.8 * term_size((120, 24)).columns), 120)
    term = Term(width, args.indent, args.compact, None if args.ansi == 'auto' else args.ansi == 'y')

    try:
        if args.less:
            # attach stdout to stdin of 'less' subprocess
            less_proc = subprocess.Popen(['less', '-cRK'], stdin=subprocess.PIPE, text=True)
            sys.stdout = less_proc.stdin

        # print news headlines
        asyncio.run(print_news(args.days or (5 if args.less else 2), term, args.icons, args.less))

    except (BrokenPipeError, KeyboardInterrupt): pass # ignore user exiting early

    finally:
        if args.less:
            less_proc.stdin.close()
            less_proc.wait()
