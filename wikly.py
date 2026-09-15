#!/usr/bin/env python3
#---------------------------------------------------------------------------------------------------
# Wikly
#---------------------------------------------------------------------------------------------------
#
# A curses reader for the Wikipedia "Portal:Current events" feed (cf. https://w.wiki/DA2e). Each day
# is fetched in a background thread & parsed into a small document model -- groups, nested items,
# styled inline cells -- which is then laid out into the reader: one tab per day, headlines grouped
# under the category headings of the portal, & every link in a headline reachable from the keyboard.
# The two axes swap over (t): a tab per category, with the days as the headings within it. Or the
# nesting can be flattened (f): only the stories themselves, with the topics above each one shown as
# a breadcrumb in the footer.
#
# There are no command line arguments; the config block below sets what is loaded & how it looks.
#
# Keys are listed in the footer & in the help overlay (?).
#
#---------------------------------------------------------------------------------------------------

import curses
import html
import json
import locale
import os
import re
import subprocess
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


#---- config ---------------------------------------------------------------------------------------

APP_NAME = "Wikly"
VERSION  = "1.0.0"

DAYS       = 7         # days of news to load, ending today
TEXT_WIDTH = 96        # widest the text column is allowed to grow
COMPACT    = False     # start without the blank lines between headlines
FLAT       = False     # start with only the stories, their topics moved to the footer
ICONS      = True      # emoji beside the category headings

WORKERS = 4 # parallel fetches
TIMEOUT = 15.0
RETRIES = 3

TOAST_SECONDS = 2.5
SPINNER       = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
BULLETS       = ("▸", "•", "◦", "·")
CRUMB         = " > "  # between the topics of a flattened story's breadcrumb

USER_AGENT = (f"wikly/{VERSION} (+https://en.wikipedia.org/wiki/Portal:Current_events) "
              f"Python-urllib/{sys.version_info.major}.{sys.version_info.minor}")

# Exhaustive; cf. https://w.wiki/DA2e.  An icon that the font draws wider than curses counts
# it (see dwidth) needs a trailing space here, to reserve the cell it spills into.
CATEGORIES = { # icon, & the short name used when the categories become tabs
    "Armed conflicts and attacks": ("⚔️ ", "Conflicts"),
    "Arts and culture":            ("🎨", "Arts"),
    "Business and economy":        ("📈", "Business"),
    "Disasters and accidents":     ("🌊", "Disasters"),
    "Health and environment":      ("🌱", "Health"),
    "International relations":     ("🌐", "Relations"),
    "Law and crime":               ("⚖️ ", "Law"),
    "Politics and elections":      ("🗳️ ", "Politics"),
    "Science and technology":      ("🧬", "Science"),
    "Sports":                      ("🏀", "Sports"),
}
OTHER = ("📰", "Other")

HINTS = ("↑↓ read · ←→ {tab} · ⏎ links · t group · f flat · / find · ? help · q quit",
         "↑↓ · ←→ {tab} · ⏎ links · / find · ? help · q quit",
         "⏎ links · / find · ? help · q quit",
         "? help · q quit")

LINK_HINTS = ("⏎ open · ↑↓ other links in this headline · q back to reading",
              "⏎ open · ↑↓ link · q back",
              "⏎ open · q back")


#---- styling --------------------------------------------------------------------------------------

# cell roles & inline flags, which the drawing code maps onto curses attributes
R_TEXT, R_HEAD, R_LINK, R_SRC, R_DIM, R_RULE, R_BULLET, R_WARN = range(8)
F_BOLD, F_ITAL, F_MARK = 1, 2, 4

AZURE, AMBER, AQUA, ROSE, GREY, INK = "73afeb", "ffbe62", "7fffd4", "e0656f", "6b6b6b", "1c1c1c"
C_BAR, C_BORDER, C_TAB, C_HEAD, C_LINK, C_DIM, C_WARN, C_MARK, C_FOCUS = range(1, 10)

_slots: dict[str, int] = {}


def _nearest256(r: int, g: int, b: int) -> int:
    """Closest xterm-256 index, for terminals that will not take an exact colour."""
    levels = (0, 95, 135, 175, 215, 255)
    palette = {i: (levels[(i - 16) // 36], levels[(i - 16) // 6 % 6], levels[(i - 16) % 6])
               for i in range(16, 232)}
    palette |= {i: (8 + (i - 232) * 10,) * 3 for i in range(232, 256)}
    return min(palette, key=lambda i: sum((a - b) ** 2 for a, b in zip(palette[i], (r, g, b))))


def ink(hex_: str) -> int:
    """Colour index for a hex triple: exact where the terminal allows, else nearest xterm-256."""
    if hex_ not in _slots:
        r, g, b = (int(hex_[i:i + 2], 16) for i in (0, 2, 4))
        if curses.can_change_color() and curses.COLORS >= 256:
            idx = 200 + len(_slots)
            curses.init_color(idx, *(round(v * 1000 / 255) for v in (r, g, b)))
        else: idx = _nearest256(r, g, b)
        _slots[hex_] = idx
    return _slots[hex_]


ROLE_ATTR: dict[int, int] = {}


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    azure, amber, aqua, rose, grey, ink_ = (ink(c) for c in (AZURE, AMBER, AQUA, ROSE, GREY, INK))

    for pair, fore, back in ((C_BAR, ink_, azure), (C_BORDER, azure, -1), (C_TAB, ink_, amber),
                             (C_HEAD, amber, -1), (C_LINK, aqua, -1), (C_DIM, grey, -1),
                             (C_WARN, rose, -1), (C_MARK, ink_, aqua), (C_FOCUS, ink_, amber)):
        curses.init_pair(pair, fore, back)

    ROLE_ATTR.update({
        R_TEXT:   0,
        R_HEAD:   curses.color_pair(C_HEAD) | curses.A_BOLD,
        R_LINK:   0, # lit up only while the headline is selected
        R_SRC:    curses.color_pair(C_DIM),
        R_DIM:    curses.color_pair(C_DIM),
        R_RULE:   curses.color_pair(C_BORDER),
        R_BULLET: curses.color_pair(C_HEAD),
        R_WARN:   curses.color_pair(C_WARN),
    })


def attr_for(cell: "Cell", selected: bool = False, focused: bool = False) -> int:
    """Curses attribute for a cell; links only take colour inside the selected headline."""
    if focused: return curses.color_pair(C_FOCUS) | curses.A_BOLD
    if cell.flags & F_MARK: return curses.color_pair(C_MARK) | curses.A_BOLD
    attr = ROLE_ATTR.get(cell.role, 0)
    if cell.role == R_LINK and selected: attr = curses.color_pair(C_LINK)
    if cell.flags & F_BOLD: attr |= curses.A_BOLD
    if cell.flags & F_ITAL: attr |= getattr(curses, "A_ITALIC", curses.A_UNDERLINE)
    return attr


#---- text measurement -----------------------------------------------------------------------------

def dwidth(text: str) -> int:
    """Cells a string occupies, counted exactly as wcwidth -- & so curses -- counts them.

    A font may draw a glyph wider than this: notably an emoji made of an East Asian
    Ambiguous codepoint plus a variation selector, such as U+2694 U+FE0F.  Curses still
    advances one cell for those, so claiming two here would misplace everything drawn
    after them; give such an icon a trailing space in CATEGORIES instead.
    """
    total = 0
    for ch in text:
        if unicodedata.category(ch) in ("Mn", "Me", "Cf"): continue
        total += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
    return total


def clip(text: str, width: int) -> str:
    """Longest prefix of text that fits in width cells."""
    if dwidth(text) <= width: return text
    out, used = "", 0
    for ch in text:
        step = dwidth(ch)
        if used + step > width: break
        out, used = out + ch, used + step
    return out


def fit(text: str, width: int, centre: bool = False, tail: bool = False) -> str:
    """Pad text to width, truncating its end -- or with tail, its start -- if it will not fit."""
    if width <= 0: return ""
    if dwidth(text) > width:
        text = "…" + clip(text[::-1], width - 1)[::-1] if tail else clip(text, width - 1) + "…"
    pad = max(0, width - dwidth(text))
    return " " * (pad // 2) + text + " " * (pad - pad // 2) if centre else text + " " * pad


def put(win, y, x, text, attr=0, limit=None):
    """Draw text at (y, x), clipped to the window (or to limit columns) & never wrapping."""
    h, w = win.getmaxyx()
    if not (0 <= y < h) or x < 0 or x >= w: return
    room = min(w - x, limit if limit is not None else w - x)
    text = clip(text, room)
    if not text: return
    try: win.addstr(y, x, text, attr)
    except curses.error: pass # the bottom-right cell always raises; nothing to do about it


#---- document model -------------------------------------------------------------------------------

class Cell(NamedTuple):
    """A run of text sharing one style. Cells are both parser output & renderer input."""
    text: str
    role: int = R_TEXT
    flags: int = 0
    url: str = ""


@dataclass
class Item:
    level: int # bullet depth, 1-based
    cells: list[Cell]

    @property
    def text(self) -> str: return "".join(c.text for c in self.cells)

    def links(self) -> list[tuple[str, str]]:
        """Distinct (label, URL) pairs in the item, in reading order."""
        seen, out = set(), []
        for cell in self.cells:
            if cell.url and cell.url not in seen:
                seen.add(cell.url)
                out.append((cell.text.strip("()").strip(), cell.url))
        return out


@dataclass
class Group:
    """A heading with items under it: a category within a day, or a day within a category."""
    title: str
    icon: str = ""
    key: str = "" # identifies the group across a regrouping
    items: list[Item] = field(default_factory=list)


@dataclass
class Edition:
    """One day's parsed news."""
    when: date
    groups: list[Group] = field(default_factory=list)


class NewsError(Exception):
    """Anything that stopped one day's news from being fetched or parsed."""


#---- wikitext parsing -----------------------------------------------------------------------------

COMMENT  = re.compile(r"<!--.*?-->", re.S)
NOINC    = re.compile(r"<noinclude>.*?</noinclude>", re.S | re.I)
TAGS     = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>"
                      r"|</?[A-Za-z][A-Za-z0-9]{0,20}(?:\s[^<>]{0,300})?/?>", re.S)
WRAPPER  = re.compile(r"^\{\{\s*current[ _]events\b", re.I)
PARAM    = re.compile(r"^\|?\s*[A-Za-z_][A-Za-z_ ]{0,20}=")
HEADING  = re.compile(r"^(?:'''(?P<b>.+?)'''|;\s*(?P<d>.+?)|={2,}\s*(?P<h>.+?)\s*={2,})$")
BULLET   = re.compile(r"^([*:#]+)[ \t]*")
MARKUP   = re.compile(r"'''''|'''|''|\[\[|\[")
BREAK    = re.compile(r"([ \t\r\n]+)") # wrap points; a nbsp is not one
WIKILINK = re.compile(r"\[\[\s*([^\[\]|]*?)\s*(?:\|\s*(.*?)\s*)?\]\]([a-zA-Z]*)", re.S)
EXTLINK  = re.compile(r"\[((?:https?:)?//[^\s\[\]]+)(?:[ \t]+([^\]]*))?\]", re.S)
QUOTES   = re.compile(r"'{2,5}")
EMBED    = re.compile(r"\s*:?\s*(?:file|image|category|media)\s*:", re.I)

WIKI_URL = "https://en.wikipedia.org/wiki/"
URL_SAFE = "/#:()',!$&+=@~*"
INTERWIKI = re.compile(r"^:\s*([a-z]{2,3}(?:-[a-z]{2,8})?):\s*(.+)$", re.S)


def cat_key(title: str) -> str:
    """Contributors vary category names slightly; fold the variations onto one key."""
    words = [w for w in re.findall(r"[a-z]+", title.lower()) if w != "and"]
    return words[0][:3] + words[-1][:3] if words else ""


CANON = {cat_key(name): name for name in CATEGORIES}


def canon(title: str) -> str: return CANON.get(cat_key(title), title)


def icon_for(title: str) -> str: return CATEGORIES.get(canon(title), OTHER)[0]


def strip_tags(text: str) -> str: return TAGS.sub("", text)


def wiki_url(dest: str) -> str:
    """Full URL for a link target; [[:he:X]] & friends point at that language's Wikipedia."""
    dest = dest.strip()
    if inter := INTERWIKI.match(dest): # inline interwiki, as {{ill}} produces
        lang, title = inter.groups()
        return f"https://{lang}.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), URL_SAFE)
    return WIKI_URL + quote(dest.lstrip(":").replace(" ", "_"), URL_SAFE)


def parse_inline(text: str, role: int = R_TEXT, flags: int = 0, url: str = "") -> list[Cell]:
    """Wikitext inline markup -> styled cells. Anything unrecognised survives as plain text."""
    out, pos, bold, ital = [], 0, False, False

    def emit(chunk, role_=None, flags_=None, url_=""):
        if chunk:
            style = flags | (F_BOLD * bold) | (F_ITAL * ital) if flags_ is None else flags_
            out.append(Cell(html.unescape(chunk), role if role_ is None else role_,
                            style, url_ or url))

    while pos < len(text):
        mark = MARKUP.search(text, pos)
        if not mark:
            emit(text[pos:])
            break
        emit(text[pos:mark.start()])
        token, pos = mark.group(), mark.end()

        if token == "[[":
            link = WIKILINK.match(text, mark.start())
            if not link: # a literal bracket before a link: [[[x]]]
                emit("[")
                pos = mark.start() + 1
            else:
                dest, label, suffix = link.group(1), link.group(2), link.group(3)
                if EMBED.match(dest): # images & categories have nothing to show
                    pos = link.end()
                    continue
                style = flags | (F_BOLD * bold) | (F_ITAL * ital)
                out += parse_inline((label or dest) + suffix, R_LINK, style, wiki_url(dest))
                pos = link.end()
        elif token == "[":
            link = EXTLINK.match(text, mark.start())
            if link:
                href = link.group(1)
                href = "https:" + href if href.startswith("//") else href
                label = QUOTES.sub("", (link.group(2) or "").strip()).strip("()")
                label = label or (urlsplit(href).hostname or href).removeprefix("www.")
                emit(f"({label})", R_SRC, flags | F_ITAL, href)
                pos = link.end()
            else:
                emit(token)
        elif token == "'''''": bold, ital = not bold, not ital
        elif token == "'''": bold = not bold
        else: ital = not ital

    return [c for c in out if c.text]


def body_lines(raw: str) -> list[str]:
    """Strip the page furniture (wrapper template, comments, parameters) off a day's wikitext."""
    text = NOINC.sub("", COMMENT.sub("", raw))
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or line in ("{{", "}}", "|", "*", "}}}}"): continue
        if WRAPPER.search(line) and not line.startswith(("*", "'")): continue
        if line.startswith("|"): line = line[1:].strip()
        if PARAM.match(line) and not line.startswith(("*", "'", ";", "=")): continue
        if line and line != "*": lines.append(line)

    if lines and lines[-1].endswith("}}") and lines[-1].count("{{") < lines[-1].count("}}"):
        tail = lines[-1][:-2].rstrip() # wrapper closed on the back of a content line
        lines[-1:] = [tail] if tail else []
    return lines


def parse_body(when: date, lines: list[str]) -> Edition:
    """Body lines -> an Edition of category groups holding nested items."""
    edition = Edition(when)
    for line in lines:
        line = strip_tags(line).strip()
        if not line: continue

        if head := HEADING.match(line):
            title = QUOTES.sub("", next(g for g in head.groups() if g)).strip()
            edition.groups.append(Group(title, icon_for(title), canon(title)))
            continue

        bullet = BULLET.match(line)
        level = len(bullet.group(1)) if bullet else 1
        cells = parse_inline(line[bullet.end():] if bullet else line)
        if not cells: continue

        if not edition.groups: edition.groups.append(Group("")) # items before any heading
        edition.groups[-1].items.append(Item(level, cells))

    return Edition(when, [g for g in edition.groups if g.items])


#---- fetching -------------------------------------------------------------------------------------

def find_templates(text: str) -> list[tuple[int, int]]:
    """Spans of balanced {{...}} groups, outermost only."""
    spans, i = [], 0
    while (i := text.find("{{", i)) != -1:
        depth, j = 0, i
        while j < len(text):
            if text.startswith("{{", j): depth, j = depth + 1, j + 2
            elif text.startswith("}}", j):
                depth, j = depth - 1, j + 2
                if depth == 0: break
            else: j += 1
        if depth: break # unbalanced; leave the rest alone

        spans.append((i, j))
        i = j
    return spans


def crude_expand(template: str) -> str:
    """Offline stand-in for a template: its positional arguments, which is usually the wording."""
    args = template.strip("{}").split("|")[1:]
    return " ".join(a.strip() for a in args if a.strip() and "=" not in a.split("[")[0])


class Wiki:
    """Fetches & parses days of the current events portal. Safe to share across threads."""

    RAW = "https://en.wikipedia.org/wiki/Portal:Current_events/%s?action=raw"
    API = "https://en.wikipedia.org/w/api.php"
    SEP = "␟" # sentinel joining a batch of templates

    def __init__(self): self.templates: dict[str, str] = {}

    def get(self, url: str, data: dict | None = None) -> str:
        """GET/POST with a descriptive user agent, retrying transient failures."""
        body = urlencode(data).encode() if data else None
        problem: Exception = NewsError("no attempt made")
        for attempt in range(RETRIES):
            try:
                request = Request(url, data=body, headers={"User-Agent": USER_AGENT})
                with urlopen(request, timeout=TIMEOUT) as response:
                    return response.read().decode("utf-8", "replace")
            except HTTPError as exc:
                if exc.code < 500 and exc.code != 429: raise
                problem = exc
            except (URLError, OSError) as exc:
                problem = exc
            time.sleep(0.4 * 2 ** attempt)

        raise problem

    def expand(self, lines: list[str]) -> list[str]:
        """Replace templates with their expansions, batching the unseen ones into one API call."""
        wanted = sorted({line[a:b] for line in lines for a, b in find_templates(line)})
        fresh = [t for t in wanted if t not in self.templates]
        if fresh: self.templates.update(self._ask(fresh))
        if not wanted: return lines

        out = []
        for line in lines:
            for a, b in reversed(find_templates(line)):
                token = line[a:b]
                line = line[:a] + self.templates.get(token, crude_expand(token)) + line[b:]
            out.append(line)
        return out

    def _ask(self, templates: list[str]) -> dict[str, str]:
        try:
            reply = self.get(self.API, {"action": "expandtemplates", "prop": "wikitext",
                                        "format": "json", "formatversion": "2",
                                        "text": f"\n{self.SEP}\n".join(templates)})
            parts = json.loads(reply)["expandtemplates"]["wikitext"].split(self.SEP)
        except (OSError, ValueError, KeyError): return {}
        if len(parts) != len(templates): return {} # a template ate the sentinel; trust none
        return {t: html.unescape(strip_tags(p)).strip() for t, p in zip(templates, parts)}

    def edition(self, when: date) -> Edition:
        """The parsed news for one day. Raises NewsError for anything the reader should see."""
        slug = f"{when.year}_{when.strftime('%B')}_{when.day}"
        try: raw = self.get(self.RAW % quote(slug))
        except HTTPError as exc:
            if exc.code == 404: return Edition(when) # the page has not been created yet
            raise NewsError(f"HTTP {exc.code}: {exc.reason}") from exc
        except URLError as exc: raise NewsError(f"{exc.reason}") from exc
        except OSError as exc: raise NewsError(str(exc)) from exc
        return parse_body(when, self.expand(body_lines(raw)))


#---- layout ---------------------------------------------------------------------------------------

class Row(NamedTuple):
    """One laid-out screen line, & the headline it belongs to (-1 for rules & blanks)."""
    cells: list[Cell]
    item: int = -1


@dataclass
class View:
    """One tab laid out at a particular width."""
    rows: list[Row] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    where: list[tuple[str, str]] = field(default_factory=list)       # group title & key, per item
    trails: list[tuple[str, ...]] = field(default_factory=list)      # the topics above each item
    spans: dict[int, tuple[int, int]] = field(default_factory=dict)  # item -> first & last row
    leads: dict[int, int] = field(default_factory=dict)              # item -> start of its block


def merge(cells: list[Cell]) -> list[Cell]:
    """Glue neighbouring cells that share a style, so rows stay cheap to draw."""
    out: list[Cell] = []
    for cell in cells:
        if out and out[-1][1:] == cell[1:]:
            out[-1] = out[-1]._replace(text=out[-1].text + cell.text)
        else: out.append(cell)
    return out


def hard_split(token: list[Cell], width: int) -> list[list[Cell]]:
    """Break an unbreakable token (a long URL, say) into width-sized pieces."""
    pieces, cur, used = [], [], 0
    for cell in token:
        for ch in cell.text:
            step = dwidth(ch)
            if used + step > width and cur:
                pieces.append(merge(cur))
                cur, used = [], 0
            cur.append(cell._replace(text=ch))
            used += step
    pieces.append(merge(cur))
    return pieces


def wrap_cells(cells: list[Cell], width: int, first: str = "", cont: str = "",
               prefix_role: int = R_BULLET) -> list[list[Cell]]:
    """Greedy word wrap over styled cells. first & cont must be the same display width."""
    avail = max(4, width - dwidth(first))

    tokens, pending = [], []
    for cell in cells:
        for part in BREAK.split(cell.text):
            if not part: continue
            if BREAK.fullmatch(part): # a non-breaking space is not a break
                if pending:
                    tokens.append(pending)
                    pending = []
            else:
                pending.append(cell._replace(text=part))
    if pending: tokens.append(pending)

    lines, cur, used, prefix = [], [], 0, first

    def flush():
        nonlocal cur, used, prefix
        lead = [Cell(prefix, prefix_role if prefix.strip() else R_TEXT)] if prefix else []
        lines.append(lead + merge(cur))
        cur, used, prefix = [], 0, cont

    for token in tokens:
        span = sum(dwidth(c.text) for c in token)
        if cur and used + 1 + span > avail: flush()

        if not cur and span > avail:            # nothing to break on: split the token itself
            pieces = hard_split(token, avail)
            for piece in pieces[:-1]:
                cur = piece
                flush()
            cur = pieces[-1]
            used = sum(dwidth(c.text) for c in cur)
            continue

        if cur:                                 # a space inside a link stays part of the link
            tail = cur[-1]
            cur.append(tail._replace(text=" ") if tail[1:] == token[0][1:] else Cell(" "))
            used += 1
        cur += token
        used += span

    if cur or not lines: flush()
    return lines


def rule(title: str, icon: str, width: int) -> list[Cell]:
    """A section heading set into a horizontal rule."""
    label = f" {icon} {title} " if icon else f" {title} "
    if dwidth(label) > width - 3: # a very narrow panel truncates the heading
        label = f" {clip(label.strip(), max(1, width - 5))}… "
    tail = max(0, width - 2 - dwidth(label))
    return [Cell("──", R_RULE), Cell(label, R_HEAD, F_BOLD), Cell("─" * tail, R_RULE)]


def render(groups: list[Group], width: int, compact: bool = False, icons: bool = True,
           flat: bool = False) -> View:
    """Lay out groups of headlines at the given text width; flat keeps only the innermost ones."""
    view = View()

    def add(cells=(), item=-1): view.rows.append(Row(list(cells), item))

    for group in groups:
        if view.rows: add()
        if group.title:
            add(rule(group.title, group.icon if icons else "", width))
            add()

        previous, above = 0, []
        for number, item in enumerate(group.items):
            while above and above[-1].level >= item.level: above.pop()
            trail = tuple(" ".join(parent.text.split()).rstrip(":") for parent in above)
            above.append(item)
            after = group.items[number + 1] if number + 1 < len(group.items) else None
            if flat and after and after.level > item.level: continue # a topic, not a story

            index = len(view.items)
            view.items.append(item)
            view.where.append((group.title, group.key))
            view.trails.append(trail)

            level = 1 if flat else item.level
            if view.rows and view.rows[-1].cells and not compact:
                if level <= 1 or level < previous: add()

            pad = "  " * min(level - 1, max(0, (width - 8) // 2))
            glyph = BULLETS[min(level, len(BULLETS)) - 1]
            head = f"{pad}{glyph} "
            role = R_BULLET if level == 1 else R_DIM
            for line in wrap_cells(item.cells, width, head, " " * dwidth(head), role):
                add(line, index)
            previous = level

    for number, row in enumerate(view.rows):  # the first & last row of each headline ...
        if row.item >= 0:
            start, end = view.spans.get(row.item, (number, number))
            view.spans[row.item] = (min(start, number), max(end, number))

    behind = 0                                # ... & the rule & blank lines ahead of it
    for item in range(len(view.items)):
        view.leads[item] = behind
        behind = view.spans[item][1] + 1

    return view


def mark(cells: list[Cell], query: str) -> list[Cell]:
    """Flag the parts of a row that match the search query, splitting cells where needed."""
    if not query: return cells
    text = "".join(c.text for c in cells).lower()
    hits = [m.span() for m in re.finditer(re.escape(query.lower()), text)]
    if not hits: return cells

    out, pos = [], 0
    for cell in cells:
        start, end = pos, pos + len(cell.text)
        cuts = sorted({start, end} | {p for hit in hits for p in hit if start < p < end})
        for a, b in zip(cuts, cuts[1:]):
            lit = any(s <= a and b <= e for s, e in hits)
            out.append(cell._replace(text=cell.text[a - start:b - start],
                                     flags=cell.flags | (F_MARK if lit else 0)))
        pos = end
    return merge(out)


#---- shell helpers --------------------------------------------------------------------------------

def open_url(url: str) -> str | None:
    """Hand a URL to the desktop's browser, detached so it cannot disturb the TUI."""
    for opener in ("xdg-open", "open", "wslview"):
        try:
            subprocess.Popen([opener, url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return None
        except OSError: continue
    return "no URL opener found (tried xdg-open, open, wslview)"


def clipboard(text: str) -> str | None:
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"], ["pbcopy"]):
        try:
            done = subprocess.run(cmd, input=text, text=True, timeout=5)
            if done.returncode == 0: return None
        except (OSError, subprocess.SubprocessError): continue
    return "no clipboard tool (tried wl-copy, xclip, xsel, pbcopy)"


def long_date(when: date) -> str: return f"{when:%A}, {when.day} {when:%B %Y}"


#---- app ------------------------------------------------------------------------------------------

@dataclass
class DayState:
    """One day's fetch: the parsed edition, what went wrong, or a job still running."""
    when: date
    edition: Edition | None = None
    error: str = ""
    future: object = None

    @property
    def key(self) -> str: return self.when.isoformat()


@dataclass
class Pane:
    """Where the reader is within one tab, & the layout it is reading."""
    sel: int = 0
    top: int = 0
    view: View | None = None
    shape: tuple = ()


HELP = [
    ("↑ ↓   j k",     "move between headlines"),
    ("PgUp PgDn",     "scroll by screenful"),
    ("g G",           "first / last headline"),
    ("← →   h l",     "previous / next tab  (also Tab, ⇧Tab)"),
    ("1 … 9",         "jump straight to tab"),
    ("t",             "regroup: tab per day, or tab per category"),
    ("f",             "flatten: only show stories, with topics in footer"),
    ("c",             "toggle compact spacing"),
    ("+",             "load another day of news"),
    ("⏎   o",         "step into links of selected headline"),
    ("↑↓ ⏎ q",        "inside: choose link, open it, step back out"),
    ("/",             "search this tab;  n N  step through matches"),
    ("y",             "copy headline to clipboard"),
    ("r   R",         "reload this day / every day"),
    ("^L",            "repaint screen"),
    ("q   Esc",       "quit"),
]


class App:
    def __init__(self, scr, wiki: Wiki, days: list[date]):
        self.scr, self.wiki = scr, wiki
        self.days = [DayState(when) for when in days]
        self.today = date.today()
        self.pool = ThreadPoolExecutor(WORKERS, thread_name_prefix="fetch")

        self.mode = "day"                     # a tab per day, or (regrouped) one per category
        self.here = {"day": "", "topic": ""}  # the tab open in each grouping
        self.panes: dict[tuple[str, str], Pane] = {}
        self.fresh = 0                        # bumped whenever the fetched news changes

        self.cap, self.compact, self.icons, self.flat = TEXT_WIDTH, COMPACT, ICONS, FLAT
        self.toast = self.link = self.typing = None
        self.search = ""
        self.helping, self.running = False, True

    # --- fetching -------------------------------------------------------
    def fetch(self, st: DayState):
        st.future, st.error, st.edition = self.pool.submit(self.wiki.edition, st.when), "", None

    def poll(self):
        """Collect finished fetches; news that has changed invalidates the laid-out views."""
        for st in self.days:
            if st.future is None or not st.future.done(): continue
            try: st.edition, st.error = st.future.result(), ""
            except NewsError as exc: st.error = str(exc)
            except Exception as exc: st.error = f"{type(exc).__name__}: {exc}"
            st.future, self.fresh = None, self.fresh + 1

    # --- tabs -----------------------------------------------------------
    def keys(self) -> list[str]:
        """One tab per day, or (regrouped) one per category that has any news."""
        if self.mode == "day": return [st.key for st in self.days]
        seen = {g.key for st in self.days if st.edition for g in st.edition.groups}
        return [name for name in CATEGORIES if name in seen] + sorted(seen - CATEGORIES.keys())

    @property
    def key(self) -> str:
        """The open tab, kept valid as days load & the grouping changes."""
        keys = self.keys()
        if not keys: return ""
        if self.here[self.mode] not in keys: self.here[self.mode] = keys[0]
        return self.here[self.mode]

    @property
    def pane(self) -> Pane: return self.panes.setdefault((self.mode, self.key), Pane())

    def day(self, key: str) -> DayState | None:
        return next((st for st in self.days if st.key == key), None)

    def scope(self) -> list[DayState]:
        """The days the open tab draws on."""
        return self.days if self.mode == "topic" else [st for st in self.days if st.key == self.key]

    def groups(self) -> list[Group]:
        """What the open tab holds: a day's categories, or a category's days."""
        if self.mode == "day":
            st = self.day(self.key)
            return st.edition.groups if st and st.edition else []
        return [Group(long_date(st.when), "", st.key, items) for st in self.days if st.edition
                and (items := [i for g in st.edition.groups if g.key == self.key for i in g.items])]

    def switch(self, delta: int):
        keys = self.keys()
        if keys: self.here[self.mode] = keys[(keys.index(self.key) + delta) % len(keys)]
        self.link = None

    def jump(self, index: int):
        keys = self.keys()
        if index < len(keys): self.here[self.mode] = keys[index]
        self.link = None

    def regroup(self):
        """Swap the axes: tabs of days with category headings, or the other way about."""
        view = self.view()
        stay = view.where[self.pane.sel][1] if view.items else ""

        self.mode = "topic" if self.mode == "day" else "day"
        if stay: self.here[self.mode] = stay
        self.link = None
        self.notify("a tab per category" if self.mode == "topic" else "a tab per day")

    def add_day(self):
        """Load one more day, older than any loaded so far."""
        st = DayState(min(s.when for s in self.days) - timedelta(days=1))
        self.days.append(st)
        self.fetch(st)
        if self.mode == "day": self.here["day"] = st.key
        self.notify(f"loading {long_date(st.when)}")

    def reload(self, everywhere=False):
        days = self.days if everywhere else self.scope()
        if not days: return
        for st in days: self.fetch(st)
        self.fresh += 1
        self.notify(f"reloading {len(days)} days" if len(days) > 1
                    else f"reloading {long_date(days[0].when)}")

    # --- layout ---------------------------------------------------------
    def column(self, w: int) -> tuple[int, int]:
        """Width & left edge of the text column, centred in the panel when there is room."""
        avail = max(10, w - 5)
        width = min(self.cap, avail)
        return width, 3 + (avail - width) // 2

    def page_rows(self) -> int: return max(1, self.scr.getmaxyx()[0] - 4)

    def view(self) -> View:
        """The open tab, laid out for this terminal; rebuilt only when something moves."""
        pane = self.pane
        shape = (self.column(self.scr.getmaxyx()[1])[0], self.compact, self.icons, self.flat,
                 self.fresh)
        if pane.view is None or pane.shape != shape:
            groups, old = self.groups(), pane.view
            pane.view = render(groups, shape[0], self.compact, self.icons, self.flat)
            pane.shape = shape
            pane.sel = self.relocate(groups, old, pane.sel, pane.view)
            self.follow()
        return pane.view

    @staticmethod
    def relocate(groups: list[Group], old: View | None, sel: int, new: View) -> int:
        """Keep the selection on the same headline across a relayout; a topic that flattening
        hides hands it on to its first story."""
        last = max(0, len(new.items) - 1)
        if not old or sel >= len(old.items): return min(sel, last)
        order = {id(item): n for n, item in enumerate(i for g in groups for i in g.items)}
        if (at := order.get(id(old.items[sel]))) is None: return min(sel, last) # refetched
        return next((n for n, item in enumerate(new.items) if order[id(item)] >= at), last)

    def follow(self):
        """Scroll the minimum needed to bring the selected headline into view."""
        pane, rows = self.pane, self.page_rows()
        if not pane.view or not pane.view.items:
            pane.top = 0
            return

        start, end = pane.view.spans[pane.sel]
        if start < pane.top:
            pane.top = pane.view.leads[pane.sel] # its section heading comes back with it
        elif end >= pane.top + rows:
            pane.top = min(start, end - rows + 1)

        pane.top = max(0, min(pane.top, max(0, len(pane.view.rows) - rows)))

    def move(self, delta: int):
        view, pane = self.view(), self.pane
        if not view.items: return
        pane.sel = max(0, min(len(view.items) - 1, pane.sel + delta))
        self.follow()

    def scroll(self, delta: int):
        """Scroll by rows, then drag the selection to the first headline still in view."""
        view, pane, rows = self.view(), self.pane, self.page_rows()
        if not view.items: return
        pane.top = max(0, min(pane.top + delta, max(0, len(view.rows) - rows)))
        visible = (i for i in range(len(view.items)) if view.spans[i][1] >= pane.top)
        pane.sel = next(visible, pane.sel)

    # --- actions --------------------------------------------------------
    def selected(self) -> Item | None:
        view = self.view()
        return view.items[self.pane.sel] if view.items else None

    def links(self) -> list[tuple[str, str]]:
        item = self.selected()
        return item.links() if item else []

    def focus_url(self) -> str:
        links = self.links()
        return links[self.link][1] if self.link is not None and self.link < len(links) else ""

    def enter_links(self):
        """Step into the selected headline; once inside, ⏎ opens the focused link."""
        if self.link is not None:
            self.launch(self.focus_url())
        elif not self.links():
            self.notify("no links in this headline", R_WARN)
        else:
            self.link = 0
            self.show_link()

    def move_link(self, delta: int):
        if links := self.links():
            self.link = (self.link + delta) % len(links)
            self.show_link()

    def show_link(self):
        """Scroll the focused link into view; a headline can be taller than the panel."""
        pane, url, rows = self.pane, self.focus_url(), self.page_rows()
        if not pane.view or not url: return

        first, last = pane.view.spans[pane.sel]
        here = next((n for n in range(first, last + 1)
                     if any(c.url == url for c in pane.view.rows[n].cells)), None)
        if here is None: return

        if here < pane.top: pane.top = here
        elif here >= pane.top + rows: pane.top = here - rows + 1

    def launch(self, url: str):
        problem = open_url(url)
        self.notify(problem or f"opened {urlsplit(url).hostname or url}",
                    R_WARN if problem else R_TEXT)

    def copy(self):
        item = self.selected()
        if not item:
            self.notify("nothing to copy", R_WARN)
            return

        problem = clipboard(item.text)
        self.notify(problem or f"copied {len(item.text)} characters",
                    R_WARN if problem else R_TEXT)

    # --- search ---------------------------------------------------------
    def hits(self) -> list[int]:
        if not self.search: return []
        needle, view = self.search.lower(), self.view()
        return [i for i, item in enumerate(view.items) if needle in item.text.lower()
                or self.flat and any(needle in topic.lower() for topic in view.trails[i])]

    def commit_search(self):
        self.search, self.typing = (self.typing or "").strip(), None
        if not self.search: return

        hits = self.hits()
        if not hits:
            self.notify(f"no match for “{self.search}”", R_WARN)
            return

        self.notify(f"{len(hits)} match{'es' * (len(hits) > 1)} for “{self.search}”")
        self.step_match(0)

    def step_match(self, delta: int):
        hits = self.hits()
        if not hits:
            self.notify("no matches in this tab", R_WARN)
            return

        pane = self.pane
        if delta > 0: pane.sel = next((i for i in hits if i > pane.sel), hits[0])
        elif delta < 0: pane.sel = next((i for i in reversed(hits) if i < pane.sel), hits[-1])
        else: pane.sel = next((i for i in hits if i >= pane.sel), hits[0])
        self.follow()

    # --- state ----------------------------------------------------------
    def notify(self, text, role=R_TEXT):
        self.toast = (text, role, time.monotonic() + TOAST_SECONDS)

    def live_toast(self):
        if self.toast and time.monotonic() >= self.toast[2]: self.toast = None
        return self.toast

    def spinner(self) -> str: return SPINNER[int(time.monotonic() * 12) % len(SPINNER)]

    # --- drawing --------------------------------------------------------
    def title(self, short=False) -> str:
        """What the top bar calls the open tab."""
        if self.mode == "topic":
            old, new = min(st.when for st in self.days), max(st.when for st in self.days)
            return (f"{len(self.days)} days" if short
                    else f"{old.day} {old:%b} – {new.day} {new:%b}")
        when = st.when if (st := self.day(self.key)) else self.today
        return f"{when:%a} {when.day} {when:%b}" if short else long_date(when)

    def draw_topbar(self, w):
        attr = curses.color_pair(C_BAR) | curses.A_BOLD
        put(self.scr, 0, 0, " " * w, attr)
        view = self.pane.view
        spin = f"{self.spinner()}  " if self.mode == "topic" and self.waiting() else ""
        tally = f"  ·  {self.pane.sel + 1}/{len(view.items)}" if view and view.items else ""
        right = f"{spin}{self.title()}{tally} "
        if dwidth(right) > w - 18: right = f"{spin}{self.title(short=True)}{tally} "

        room = max(0, w - dwidth(right) - 2)
        for left in (f"{APP_NAME} {VERSION}  ·  Wikipedia current events",
                     f"{APP_NAME} {VERSION}", APP_NAME, ""):
            if dwidth(left) <= room: break
        put(self.scr, 0, 1, left, attr, room)
        put(self.scr, 0, max(0, w - dwidth(right)), right, attr)

    def tab_text(self, key: str, short: bool) -> str:
        if self.mode == "topic":
            icon, name = CATEGORIES.get(key, OTHER)
            return icon if short else f"{icon} {name}"
        when = date.fromisoformat(key)
        if short: return str(when.day)
        if when == self.today: return "Today"
        if when == self.today - timedelta(days=1): return "Yesterday"
        return f"{when:%a} {when.day}"

    def tab_mark(self, key: str) -> str:
        """A spinner or a warning beside a day tab that is loading or failed."""
        if self.mode == "topic" or not (st := self.day(key)): return ""
        if st.future is not None: return " " + self.spinner()
        return " !" if st.error else ""

    def draw_tabs(self, y, w):
        """Tabs sunk into the panel's top border, windowed when they cannot all fit."""
        if not (keys := self.keys()): return
        avail = max(0, w - 4)
        labels = [f" {self.tab_text(k, False)}{self.tab_mark(k)} " for k in keys]
        if sum(dwidth(t) + 1 for t in labels) > avail: # shorten all but the tab being read
            labels = [f" {self.tab_text(k, k != self.key)}{self.tab_mark(k)} " for k in keys]

        low = high = keys.index(self.key) # widen the window of tabs while they still fit
        used = dwidth(labels[low])
        while True:
            grew = False
            if high + 1 < len(labels) and used + dwidth(labels[high + 1]) + 1 <= avail:
                high, grew = high + 1, True
                used += dwidth(labels[high]) + 1
            if low > 0 and used + dwidth(labels[low - 1]) + 1 <= avail:
                low, grew = low - 1, True
                used += dwidth(labels[low]) + 1
            if not grew: break

        x = 2
        if low > 0:
            put(self.scr, y, x, "‹", curses.color_pair(C_DIM))
            x += 2
        for i in range(low, high + 1):
            attr = (curses.color_pair(C_TAB) | curses.A_BOLD if keys[i] == self.key
                    else curses.color_pair(C_DIM))
            put(self.scr, y, x, labels[i], attr, max(0, w - 2 - x))
            x += dwidth(labels[i]) + 1
        if high + 1 < len(labels):
            put(self.scr, y, min(x, w - 3), "›", curses.color_pair(C_DIM))

    def waiting(self) -> list[DayState]:
        return [st for st in self.scope() if st.future is not None]

    def broken(self) -> list[DayState]:
        return [st for st in self.scope() if st.error]

    def draw_panel(self, top, bottom, w):
        border = curses.color_pair(C_BORDER)
        put(self.scr, top, 0, "╭" + "─" * (w - 2) + "╮", border)
        put(self.scr, bottom, 0, "╰" + "─" * (w - 2) + "╯", border)
        for y in range(top + 1, bottom):
            put(self.scr, y, 0, "│", border)
            put(self.scr, y, w - 1, "│", border)
        self.draw_tabs(top, w)

        rows, view = bottom - top - 1, self.view()

        if view.items:
            self.draw_rows(top, rows, w, view)
            self.draw_scrollbar(top + 1, rows, w, len(view.rows))
        elif late := self.waiting():
            self.draw_notice(top, rows, w,
                             [(f"{self.spinner()}  fetching {long_date(late[0].when)}…", R_DIM)])
        elif bad := self.broken():
            self.draw_notice(top, rows, w, [(f"could not fetch {long_date(bad[0].when)}", R_WARN),
                                            (bad[0].error, R_DIM), ("", 0),
                                            ("press r to retry", R_DIM)])
        else: self.draw_notice(top, rows, w, [("no headlines here yet", R_DIM)])

    def draw_notice(self, top, rows, w, lines):
        for i, (text, role) in enumerate(lines):
            y = top + 1 + max(0, (rows - len(lines)) // 2) + i
            if y < top + 1 + rows:
                put(self.scr, y, 1, fit(text, w - 2, centre=True), ROLE_ATTR.get(role, 0), w - 2)

    def draw_rows(self, top, rows, w, view):
        """The visible slice of the tab: its gutter bar, its text, its search & link marks."""
        pane, (_, x0), limit = self.pane, self.column(w), w - 1
        first, last = view.spans.get(pane.sel, (-1, -1))
        inside, focus = self.link is not None, self.focus_url()
        bar = ("█", curses.color_pair(C_LINK)) if inside else ("▌", curses.color_pair(C_HEAD))

        for offset in range(rows):
            index = pane.top + offset
            if index >= len(view.rows): break
            y, row = top + 1 + offset, view.rows[index]

            live = first <= index <= last
            if live: put(self.scr, y, 1, bar[0], bar[1] | curses.A_BOLD)

            x = x0
            for cell in mark(row.cells, self.search):
                if x >= limit: break
                hot = live and bool(focus) and cell.url == focus
                put(self.scr, y, x, cell.text, attr_for(cell, live, hot), limit - x)
                x += dwidth(cell.text)

    def draw_scrollbar(self, y0, rows, w, total):
        if total <= rows: return
        size = max(1, rows * rows // total)
        start = (self.pane.top * (rows - size)) // max(1, total - rows)
        for row in range(rows):
            glyph = "█" if start <= row < start + size else "│"
            put(self.scr, y0 + row, w - 1, glyph, curses.color_pair(C_BORDER))

    def breadcrumb(self) -> str:
        """The selected headline's group, or when flattened the topics it was nested under."""
        view = self.pane.view
        if not view or not view.items: return ""
        if self.flat and (trail := view.trails[self.pane.sel]): return CRUMB.join(trail)
        return view.where[self.pane.sel][0] or "Headlines"

    def draw_footer(self, y, w):
        """Toast, else the focused URL, else where the selected headline sits; hints right."""
        if self.typing is not None:
            put(self.scr, y, 1, fit(f"/{self.typing}▏", w - 2), curses.color_pair(C_HEAD), w - 2)
            return
        toast, tail = self.live_toast(), False
        if toast:
            left, attr = f"● {toast[0]}", ROLE_ATTR.get(toast[1], 0) or curses.A_BOLD
        elif self.link is not None:
            left, attr = self.focus_url(), curses.color_pair(C_LINK)
        else: # a breadcrumb gives up its outermost topics first
            left, attr, tail = self.breadcrumb(), curses.color_pair(C_DIM), self.flat

        tiers = LINK_HINTS if self.link is not None else HINTS
        hints = next((h for h in tiers if dwidth(h) + 12 <= w), tiers[-1]).format(tab=self.mode)
        x = max(1, w - dwidth(hints) - 2) # slack: ⏎ & ↑↓ are ambiguous-width
        if toast and dwidth(left) > x - 3:
            x = w # a long toast keeps the footer to itself
        else:
            put(self.scr, y, x, hints, curses.color_pair(C_DIM), w - 1 - x)
        if toast or x - 3 > 8:
            put(self.scr, y, 1, fit(left, x - 3, tail=tail), attr, x - 3)

    def draw_help(self, h, w):
        """The key list, in a rounded box over the panel."""
        pad = max(dwidth(k) for k, _ in HELP)
        lines = [""] + [f"  {k}{' ' * (pad - dwidth(k))}   {what}" for k, what in HELP]
        lines += ["", "  any key to dismiss"]

        bw = max(24, min(w - 6, max(dwidth(t) for t in lines) + 4))
        bh = min(h - 2, len(lines) + 2)
        y0, x0 = max(0, (h - bh) // 2), max(0, (w - bw) // 2)
        border = curses.color_pair(C_BORDER)
        put(self.scr, y0, x0, "╭" + "─" * (bw - 2) + "╮", border)
        put(self.scr, y0 + bh - 1, x0, "╰" + "─" * (bw - 2) + "╯", border)
        put(self.scr, y0, x0 + 2, f" {APP_NAME} keys ", curses.color_pair(C_TAB) | curses.A_BOLD)

        for i, text in enumerate(lines[:bh - 2]):
            y = y0 + 1 + i
            put(self.scr, y, x0, "│", border)
            put(self.scr, y, x0 + bw - 1, "│", border)
            dim = curses.color_pair(C_DIM) if i == len(lines) - 1 else 0
            put(self.scr, y, x0 + 1, fit(text, bw - 2), dim, bw - 2)

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        if h < 8 or w < 34: put(self.scr, 0, 0, "terminal too small")
        else:
            self.view() # relayout if the terminal changed
            self.draw_topbar(w)
            self.draw_panel(1, h - 2, w)
            self.draw_footer(h - 1, w)
            if self.helping: self.draw_help(h, w)
        self.scr.noutrefresh()
        curses.doupdate()

    # --- input ----------------------------------------------------------
    def type_search(self, key, ch):
        if key in (10, 13): self.commit_search()
        elif key == 27: self.typing = None
        elif key in (curses.KEY_BACKSPACE, 127, 8): self.typing = self.typing[:-1]
        elif isinstance(ch, str) and ch.isprintable(): self.typing += ch

    def inside_links(self, key):
        """Keys while stepping through the links of one headline."""
        onward = (curses.KEY_DOWN, ord("j"), curses.KEY_RIGHT, ord("l"), 9)
        backward = (curses.KEY_UP, ord("k"), curses.KEY_LEFT, ord("h"), curses.KEY_BTAB)

        if key in (10, 13, ord("o")): self.enter_links() # ⏎ again: open the focused link
        elif key in onward: self.move_link(1)
        elif key in backward: self.move_link(-1)
        elif key in (27, ord("q")): self.link = None

    def handle(self, key, ch):
        if self.typing is not None: return self.type_search(key, ch)
        if self.helping:
            self.helping = False
            return
        if self.link is not None: return self.inside_links(key)

        rows = self.page_rows()
        if key == 27 and self.search:
            self.search = ""
            self.notify("search cleared", R_DIM)
        elif key in (ord("q"), 27): self.running = False
        elif key in (curses.KEY_DOWN, ord("j")): self.move(1)
        elif key in (curses.KEY_UP, ord("k")): self.move(-1)
        elif key in (curses.KEY_NPAGE, 6): self.scroll(rows - 1)
        elif key in (curses.KEY_PPAGE, 2): self.scroll(1 - rows)
        elif key == 4: self.scroll(rows // 2)
        elif key == 21: self.scroll(-rows // 2)
        elif key in (curses.KEY_HOME, ord("g")):
            self.pane.sel = 0
            self.follow()
        elif key in (curses.KEY_END, ord("G")):
            self.pane.sel = max(0, len(self.view().items) - 1)
            self.follow()
        elif key in (curses.KEY_RIGHT, ord("l"), 9): self.switch(1)
        elif key in (curses.KEY_LEFT, ord("h"), curses.KEY_BTAB): self.switch(-1)
        elif ord("1") <= key <= ord("9"): self.jump(key - ord("1"))
        elif key in (10, 13, ord("o")): self.enter_links()
        elif key == ord("t"): self.regroup()
        elif key in (ord("+"), ord("=")): self.add_day()
        elif key == ord("/"): self.typing = ""
        elif key == ord("n"): self.step_match(1)
        elif key == ord("N"): self.step_match(-1)
        elif key == ord("y"): self.copy()
        elif key == ord("f"):
            self.flat = not self.flat
            self.notify("only the stories, topics in the footer" if self.flat
                        else "stories nested under their topics", R_DIM)
        elif key == ord("c"):
            self.compact = not self.compact
            self.notify("compact spacing" if self.compact else "roomy spacing", R_DIM)
        elif key == ord("r"): self.reload()
        elif key == ord("R"): self.reload(everywhere=True)
        elif key == ord("?"): self.helping = True
        elif key == 12: self.scr.redrawwin() # ^L, for a screen left dirty by something else

    def run(self):
        for st in self.days: self.fetch(st)

        try:
            while self.running:
                self.poll()
                self.draw()
                busy = any(st.future is not None for st in self.days)
                self.scr.timeout(80 if busy else (200 if self.toast else -1))
                try: ch = self.scr.get_wch()
                except curses.error: continue # timed out; loop round & repaint

                key = ord(ch) if isinstance(ch, str) else ch
                if key == curses.KEY_RESIZE:
                    self.scr.redrawwin()
                    continue
                self.handle(key, ch)
        finally:
            self.pool.shutdown(wait=False, cancel_futures=True)


#---- entry ----------------------------------------------------------------------------------------

def main() -> int:
    if sys.argv[1:]:
        sys.exit(f"wikly takes no arguments; see the config block in "
                 f"{os.path.basename(__file__)}")
    if not sys.stdout.isatty(): sys.exit("wikly needs an interactive terminal")

    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    today = date.today()
    days = [today - timedelta(days=n) for n in range(DAYS)]

    def reader(scr):
        init_colors()
        try: curses.curs_set(0)
        except curses.error: pass
        App(scr, Wiki(), days).run()

    curses.wrapper(reader)
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
