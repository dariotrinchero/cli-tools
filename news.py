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

    def wrap(self, line, list_lvl=0):
        indent_args = {}
        if list_lvl > 0:
            indent_args['initial_indent'] = ' ' * self.indent * (list_lvl - 1) + '\u2022 '
            indent_args['subsequent_indent'] = ' ' * (self.indent * (list_lvl - 1) + 2)
        return textwrap.fill(line, self.width, break_long_words=False, **indent_args)

    def line_space(self, line):
        return line if self.compact else '\n' + line


class WikiNews:
    ''' Class for parsing Wikipedia news, as per specification (https://w.wiki/DA2e). '''

    CATEGORIES = { # headline categories
        # this list is exhaustive; cf. https://w.wiki/DA2e
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

    DELIM = '\x1F' # unit separator; delimits links with hidden URLs

    RE = { # patterns for parsing news
        'bold':    re.compile("'''(.+?)'''"),
        'bullet':  re.compile('^\*+(?=[^*])'),
        'heading': re.compile("^'''(.+)'''$"),
        'ital':    re.compile("''(.+?)''"),
        'link':    re.compile(r'(\[\[(.+?)(\|(.+?))?\]\])|(\[([^ []+)( (\(.+?\)))?\])'),
        'linktxt': re.compile(f'{DELIM}(.+?){DELIM}')
    }

    def __init__(self, day, heading_icons=True):
        self.heading_icons = heading_icons

        # retrieve news for given day from Wikipedia as list of lines
        day_str = day.strftime('%Y_%B_%d')
        try:
            req = urlopen(f'https://en.wikipedia.org/wiki/Portal:Current_events/{day_str}?action=raw')
        except (HTTPError, URLError) as err:
            exit(f'Unable to retrieve news for {day.strftime("%-d %B %Y")}: {err}')
        self.news = bytes(req.read()).decode('utf-8').split('\n')[3:-1] # trim non-content

    def format(self, formatter):
        ''' Yield news lines, formatted using given formatter. '''
        for l, line in enumerate(self.news):
            # conceal link URLs for better line wrapping
            urls = []
            line = WikiNews.RE['link'].sub(lambda m: WikiNews.__hide_url(m, urls), line)

            # bold, italics & headings
            line = self.__format_heading(line, formatter)
            line = WikiNews.RE['bold'].sub(lambda m: formatter.bold(m.group(1)), line)
            line = WikiNews.RE['ital'].sub(lambda m: formatter.ital(m.group(1)), line)

            # bullet points & line wrapping
            lvl = self.__list_lvl(l)
            line = formatter.wrap(line[lvl:].strip(' '), lvl)

            # add spacing if level is not between levels of adjacent lines
            if lvl > 0 and not (0 < self.__list_lvl(l - 1) < lvl < self.__list_lvl(l + 1)):
                line = formatter.line_space(line)

            # restore link URLs
            line = WikiNews.RE['linktxt'].sub(lambda m: formatter.link(urls.pop(), m.group(1)), line)

            yield line

    def is_blank(self):
        ''' Return whether there is no news for given day. '''
        return self.news == ['*'] # default empty template

    def __list_lvl(self, line):
        ''' Get indent level of bullet on given line (0 if out of range, or not list item). '''
        if not 0 < line < len(self.news): return 0
        bullets = WikiNews.RE['bullet'].search(self.news[line])
        return 0 if not bullets else bullets.end()

    def __format_heading(self, line, formatter, color=(255, 255, 170)):
        ''' Apply appropriate formatting to line if heading; else leave unchanged. '''
        m = WikiNews.RE['heading'].match(line)
        if m is None: return line
        heading = m.group(1).capitalize()
        if heading not in WikiNews.CATEGORIES: return line

        icon = WikiNews.CATEGORIES[heading] + ' ' if self.heading_icons else ''
        return icon + formatter.bold(formatter.color(heading, color))

    @staticmethod
    def __hide_url(m, urls):
        ''' Take regex match for link, & list of URLs; append URL to list, & return link text. '''
        url, text = (m.group(2), m.group(4)) if m.group(2) else (m.group(6), m.group(8))

        # get URL for internal wikilink
        if not WikiNews.__valid_url(url):
            if text is None: text = url
            target = quote(url.replace(' ', '_'))
            url = f'https://en.wikipedia.org/wiki/{target}'

        # wrap display text in delimiters & make spaces non-breaking
        if text is None: text = url
        text = WikiNews.DELIM + text.replace(' ', '\u00A0') + WikiNews.DELIM

        # record hidden URL in list & return display text
        urls.insert(0, url)
        return text

    @staticmethod
    def __valid_url(url):
        ''' Return whether given string is well-formed URL. '''
        try:
            parsed = urlparse(url)
            return all([parsed.scheme, parsed.netloc])
        except AttributeError: return False


def print_news(days, term, heading_icons=True):
    ''' Print news headlines from Wikipedia for given number of days. '''
    today = date.today()
    for d in range(days):
        day = today - timedelta(days=d)

        if d > 0: print() # separating line
        print(term.heading(day.strftime('%A, %-d %B %Y')) + '\n')
        print(term.fmt('fetching...\r', Term.DIM, Term.ITAL) * term.ansi, end='')
        sys.stdout.flush()

        news = WikiNews(day, heading_icons)
        print(term.ital("(no news)  ") if news.is_blank() else '\n'.join(news.format(term)))


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
