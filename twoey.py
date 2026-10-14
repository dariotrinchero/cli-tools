#!/usr/bin/env python3
#---------------------------------------------------------------------------------------------------
# Twoey
#---------------------------------------------------------------------------------------------------
#
# A curses reader for YouTube subscriptions, fed by each channel's RSS feed -- no account, no API
# key. Every channel listed in ~/.twoey-urls is fetched in the background, & their videos merged
# into one list, newest first, with the date each came out down the left: the New tab holds those
# not yet watched, the All tab everything from the last two months. Opening a video hands it to the
# browser & marks it watched. The Channels tab shows how each subscription last fetched -- YouTube's
# feeds fail now & then, so a failure is flagged in red, with its reason -- & is where channels are
# added (paste a link to any channel, video or playlist) & removed.
#
# The subscriptions file is plain text, one channel per line, & fine to edit by hand (e).
#
# There are no command line arguments; the config block below sets what is loaded & how it looks.
#
# Keys are listed in the footer & in the help overlay (?).
#
#---------------------------------------------------------------------------------------------------

import curses
import json
import locale
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import lru_cache
from http.client import HTTPException, HTTPSConnection
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPSHandler, Request, build_opener


#---- config ---------------------------------------------------------------------------------------

APP_NAME = "Twoey"
VERSION  = "0.1.0"

URLS_FILE  = "~/.twoey-urls"             # the subscriptions; a symlink (into dotfiles, say) is fine
STATE_FILE = "~/.local/state/twoey.json" # the videos fetched so far, & which have been watched

MAX_AGE     = 62       # days of videos to keep
SKIP_SHORTS = True
LIST_WIDTH  = 116      # widest the list is allowed to grow
NAME_WIDTH  = 28       # widest the column of channel names is allowed to grow
INFO_WIDTH  = 104      # widest the details box (i) is allowed to grow
REFRESH     = 30       # minutes between refreshes while open; 0 for only at start & on request

WORKERS = 4 # parallel fetches
TIMEOUT = 15.0
RETRIES = 3 # YouTube's feeds fail a fair amount of the time, but usually work on another try

TOAST_SECONDS = 2.5
SPINNER       = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

USER_AGENT = f"Mozilla/5.0 (X11; Linux x86_64) twoey/{VERSION}"

HINTS = {
    "videos":   ("⏎ watch · space new/watched · i info · s sort · a add · / filter · "
                 "r refresh · ? help · q quit",
                 "⏎ watch · space new/watched · i info · s sort · / filter · ? help · q quit",
                 "⏎ watch · i info · ? help · q quit",
                 "? help · q quit"),
    "channels": ("⏎ videos · i info · a add · d unsubscribe · e edit list · / filter · "
                 "r refresh · ? help · q quit",
                 "⏎ videos · i info · a add · d unsubscribe · ? help · q quit",
                 "? help · q quit"),
    "feed":     ("⏎ watch · space new/watched · i info · d unsubscribe · ? help · q back",
                 "⏎ watch · space new/watched · i info · ? help · q back",
                 "? help · q back"),
}


#---- styling --------------------------------------------------------------------------------------

# Audrey II, who must be fed: the leaf & new growth of the plant, the pink & red of her maw
LEAF, LIME, PETAL, MAW = "5cb85a", "d4f06a", "ff78b0", "ff4f5e"
PALE, SAGE, GREY, INK = "e4e8dc", "8fa08f", "6b6b6b", "1c1c1c"
C_BAR, C_BORDER, C_SEL, C_HEAD, C_NAME, C_NEW, C_SEEN, C_DIM, C_WARN, C_ALARM = range(1, 11)

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


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    leaf, lime, petal, maw, pale, sage, grey, ink_ = (
        ink(c) for c in (LEAF, LIME, PETAL, MAW, PALE, SAGE, GREY, INK))

    for pair_, fore, back in ((C_BAR, ink_, leaf), (C_BORDER, leaf, -1), (C_SEL, ink_, lime),
                              (C_HEAD, lime, -1), (C_NAME, petal, -1), (C_NEW, pale, -1),
                              (C_SEEN, sage, -1), (C_DIM, grey, -1), (C_WARN, maw, -1),
                              (C_ALARM, ink_, maw)):
        curses.init_pair(pair_, fore, back)


def pair(number: int, bold: bool = False) -> int:
    return curses.color_pair(number) | (curses.A_BOLD if bold else 0)


#---- text measurement -----------------------------------------------------------------------------

@lru_cache(maxsize=8192)
def dwidth(text: str) -> int:
    """Cells a string occupies, counted as wcwidth -- & so curses -- counts them. Nearly all that
    is drawn passes through here, much of it unchanged from the last repaint; hence the cache, &
    the shortcut for plain ASCII."""
    if text.isascii(): return len(text)
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


def wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap that keeps paragraph breaks, & splits words too long for a line."""
    width, lines = max(1, width), []
    for para in text.splitlines():
        words, line = para.split(), ""
        if not words:
            if lines and lines[-1]: lines.append("") # runs of blank lines collapse to one
            continue
        for word in words:
            while dwidth(word) > width: # a long URL, say
                if line: lines.append(line)
                head = clip(word, width) or word[0]
                lines.append(head)
                line, word = "", word[len(head):]
            if not line: line = word
            elif dwidth(line) + 1 + dwidth(word) <= width: line += " " + word
            else: lines, line = lines + [line], word
        if line: lines.append(line)
    while lines and not lines[-1]: lines.pop()
    return lines


def put(win, y, x, text, attr=0, limit=None):
    """Draw text at (y, x), clipped to the window (or to limit columns) & never wrapping."""
    h, w = win.getmaxyx()
    if not (0 <= y < h) or x < 0 or x >= w: return
    room = min(w - x, limit if limit is not None else w - x)
    text = clip(text, room)
    if not text: return
    try: win.addstr(y, x, text, attr)
    except curses.error: pass # the bottom-right cell always raises; nothing to do about it


def count(n: int, noun: str) -> str: return f"{n} {noun}{'s' * (n != 1)}"


def ago(seconds: float) -> str:
    for unit, size in (("y", 31557600), ("mo", 2629800), ("w", 604800), ("d", 86400),
                       ("h", 3600), ("m", 60)):
        if seconds >= size: return f"{int(seconds // size)}{unit} ago"
    return "just now"


DATE_WIDTH = 6


def short_date(stamp: float) -> str:
    """13 Oct, with the day of the month right-aligned so that a column of them lines up; no
    year, since nothing is kept long enough for one to be needed."""
    when = datetime.fromtimestamp(stamp)
    return f"{when.day:>2} {when:%b}"


def brief(error: str) -> str:
    """A failure in a word or two: its HTTP status code, else the start of its message."""
    if code := re.match(r"HTTP (\d{3})", error): return code[1]
    if "name resolution" in error or "not known" in error: return "no connection" # DNS, offline
    words = re.sub(r"^\[Errno [^]]*\]\s*", "", error).split("(")[0].split()
    while len(words) > 1 and dwidth(" ".join(words)) > 18: words.pop()
    return clip(" ".join(words), 18)


#---- model ----------------------------------------------------------------------------------------

CHANNEL_ID  = re.compile(r"UC[\w-]{22}")
PLAYLIST_ID = re.compile(r"(?:PL|UU|OL|FL|LL)[\w-]{10,}")


def feed_url(sub: str) -> str:
    """Channel IDs start UC; anything else is taken for a playlist."""
    kind = "channel_id" if CHANNEL_ID.fullmatch(sub) else "playlist_id"
    return f"https://www.youtube.com/feeds/videos.xml?{kind}={sub}"


def page_url(sub: str) -> str:
    if CHANNEL_ID.fullmatch(sub): return f"https://www.youtube.com/channel/{sub}"
    return f"https://www.youtube.com/playlist?list={sub}"


@dataclass
class Channel:
    """One subscription -- a channel, or a playlist -- as listed in the subscriptions file."""
    id: str
    name: str = ""
    group: str = ""


@dataclass
class Video:
    id: str
    channel: str # the subscription it came in by
    title: str
    published: float
    description: str = ""
    views: int = 0
    new: bool = True

    @property
    def url(self) -> str: return f"https://www.youtube.com/watch?v={self.id}"


class FetchError(Exception):
    """Anything that stopped a feed or a link from being fetched or understood."""


#---- subscriptions file ---------------------------------------------------------------------------

URLS_HEADER = """\
# Twoey subscriptions: one per line, a channel ID (or a playlist ID) & then the name to show.
# A line starting "##" begins a group. Twoey adds & removes lines here as channels are
# subscribed to & unsubscribed from in the app, & leaves everything else alone.
"""

ENTRY = re.compile(r"([\w-]{10,64})(?:\s+(.*))?")


def write_file(path: str, text: str):
    """Replace a file's contents atomically -- through a symlink, rather than over it."""
    real = os.path.realpath(path)
    os.makedirs(os.path.dirname(real), exist_ok=True)
    temp = f"{real}.{os.getpid()}.tmp"
    with open(temp, "w", encoding="utf-8") as f: f.write(text)
    try: os.chmod(temp, os.stat(real).st_mode & 0o7777)
    except FileNotFoundError: pass
    os.replace(temp, real)


class Urls:
    """The subscriptions file, edited a line at a time so that comments & layout survive."""

    def __init__(self, path: str):
        self.path = path
        self.lines: list[str] = []
        self.channels: list[Channel] = []
        self.by_id: dict[str, Channel] = {}
        self.where: dict[str, int] = {} # subscription -> its line
        self.problems: list[str] = []

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f: self.lines = f.read().splitlines()
        except FileNotFoundError: self.lines = URLS_HEADER.splitlines()
        self.parse()

    def parse(self):
        self.channels, self.by_id, self.where, self.problems, group = [], {}, {}, [], ""
        for n, line in enumerate(self.lines):
            text = line.strip()
            if text.startswith("##"): group = text.strip("#").strip()
            elif not text or text.startswith("#"): continue
            elif not (entry := ENTRY.fullmatch(text)):
                self.problems.append(f"{os.path.basename(self.path)} line {n + 1}: "
                                     f"not a channel ID (add links with a)")
            elif entry[1] not in self.where:
                channel = Channel(entry[1], (entry[2] or "").strip(), group)
                self.channels.append(channel)
                self.by_id[channel.id], self.where[channel.id] = channel, n

    def save(self):
        write_file(self.path, "\n".join(self.lines) + "\n")
        self.parse()

    def add(self, channel: Channel, after: str = ""):
        """List a new subscription after the given one (& so in its group), or else at the end."""
        self.load() # pick up any edits made since
        line = f"{channel.id}  {channel.name}".strip()
        if after in self.where: self.lines.insert(self.where[after] + 1, line)
        else: self.lines.append(line)
        self.save()

    def remove(self, sub: str):
        self.load()
        if sub in self.where:
            del self.lines[self.where[sub]]
            self.save()


#---- remembered state -----------------------------------------------------------------------------

class Store:
    """What Twoey remembers between runs: the videos fetched so far & which of them are new, &
    when each subscription last fetched."""

    def __init__(self, path: str):
        self.path = path
        self.videos: dict[str, Video] = {}
        self.titles: dict[str, str] = {}    # each feed's own title, for subscriptions with no name
        self.fetched: dict[str, float] = {} # last successful fetch of each subscription
        self.dirty = False

    def load(self) -> str:
        """Read the state back in; returns what went wrong, if anything did."""
        try:
            with open(self.path, encoding="utf-8") as f: data = json.load(f)
            self.videos = {key: Video(key, **fields) for key, fields in data["videos"].items()}
            self.titles, self.fetched = dict(data["titles"]), dict(data["fetched"])
        except FileNotFoundError: pass
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            self.videos, self.titles, self.fetched = {}, {}, {}
            try: os.replace(self.path, self.path + ".broken")
            except OSError: pass
            return f"could not read {self.path} ({exc}); moved it aside to .broken"
        return ""

    def save(self):
        videos = {v.id: {k: val for k, val in asdict(v).items() if k != "id"}
                  for v in self.videos.values()}
        write_file(self.path, json.dumps({"titles": self.titles, "fetched": self.fetched,
                                          "videos": videos}, ensure_ascii=False))
        self.dirty = False

    def merge(self, sub: str, title: str, videos: list[Video], cutoff: float) -> int:
        """Take in a fetch of one subscription; returns how many of its videos are new to us."""
        found = 0
        for video in videos:
            if video.published < cutoff: continue # forgotten already; it must not come back new
            if known := self.videos.get(video.id):
                known.title, known.views = video.title, video.views or known.views
                known.description = video.description or known.description
            else:
                self.videos[video.id] = video
                found += 1
        if title: self.titles[sub] = title
        self.fetched[sub], self.dirty = time.time(), True
        return found

    def prune(self, subs: set[str], cutoff: float):
        """Forget videos past the age limit, & everything about channels no longer subscribed to."""
        stale = [k for k, v in self.videos.items() if v.channel not in subs or v.published < cutoff]
        for key in stale: del self.videos[key]
        for table in (self.titles, self.fetched):
            for sub in [s for s in table if s not in subs]: del table[sub]
        if stale: self.dirty = True


#---- fetching -------------------------------------------------------------------------------------

NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "m": "http://search.yahoo.com/mrss/"}
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en",
           "Cookie": "SOCS=CAI"} # declines YouTube's cookies, which skips the EU consent page
PAGE_IDS = (re.compile(r'<link rel="alternate" type="application/rss\+xml"[^>]*?'
                       r'channel_id=(UC[\w-]{22})'),
            re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/'
                       r'(UC[\w-]{22})"'),
            re.compile(r'"externalId":"(UC[\w-]{22})"'))
LINKISH = re.compile(r"youtu\.?be|^@[\w.-]+$|^UC[\w-]{22}$")


def connect(address, timeout=socket.getdefaulttimeout(), source_address=None, **_):
    """socket.create_connection, but trying IPv4 addresses first: where IPv6 is routed but does
    not get through, urllib otherwise waits out the whole timeout on every request (curl
    races the two instead)."""
    host, port = address
    problem: OSError = OSError(f"could not resolve {host}")
    for *_, where in sorted(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM),
                            key=lambda info: info[0] != socket.AF_INET):
        try: return socket.create_connection(where[:2], timeout, source_address)
        except OSError as exc: problem = exc
    raise problem


class IPv4First(HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = connect # http.client sets this per connection, in __init__


class IPv4FirstHandler(HTTPSHandler):
    def https_open(self, req): return self.do_open(IPv4First, req, context=self._context)


OPENER = build_opener(IPv4FirstHandler)


def get(url: str, flaky: bool = False) -> bytes:
    """GET with retries for anything transient -- which, from the flaky feeds, includes a 404."""
    problem = FetchError("no attempt made")
    for attempt in range(RETRIES):
        try:
            with OPENER.open(Request(url, headers=HEADERS), timeout=TIMEOUT) as response:
                return response.read()
        except HTTPError as exc:
            problem = FetchError(f"HTTP {exc.code} {exc.reason}")
            if exc.code < 500 and exc.code != 429 and not (flaky and exc.code == 404):
                raise problem from exc
        except URLError as exc: problem = FetchError(str(exc.reason))
        except (OSError, HTTPException) as exc: problem = FetchError(str(exc) or type(exc).__name__)
        if attempt + 1 < RETRIES: time.sleep(0.5 * 2 ** attempt)
    raise problem


def read_feed(sub: str) -> tuple[str, list[Video]]:
    """A subscription's feed -> its title & latest videos (YouTube gives the last fifteen)."""
    # the throwaway parameter gets past any stale copy of the feed in YouTube's cache
    data = get(f"{feed_url(sub)}&_={int(time.time())}", flaky=True)
    try: root = ET.fromstring(data)
    except ET.ParseError as exc: raise FetchError(f"unreadable feed ({exc})") from exc
    if root.tag != f"{{{NS['a']}}}feed": raise FetchError("not a YouTube feed")

    videos = []
    for entry in root.iterfind("a:entry", NS):
        vid, link = entry.findtext("yt:videoId", "", NS), entry.find("a:link", NS)
        if not vid: continue
        if SKIP_SHORTS and link is not None and "/shorts/" in link.get("href", ""): continue
        try: published = datetime.fromisoformat(entry.findtext("a:published", "", NS)).timestamp()
        except ValueError: continue
        stats = entry.find("m:group/m:community/m:statistics", NS)
        views = stats.get("views", "") if stats is not None else ""
        videos.append(Video(vid, sub, " ".join(entry.findtext("a:title", "", NS).split()),
                            published,
                            entry.findtext("m:group/m:description", "", NS).strip(),
                            int(views) if views.isdigit() else 0))
    return " ".join(root.findtext("a:title", "", NS).split()), videos


def video_owner(vid: str) -> str:
    """The channel page of whoever uploaded a video, from YouTube's oEmbed endpoint."""
    link = f"https://www.youtube.com/watch?v={vid}"
    try: reply = json.loads(get("https://www.youtube.com/oembed?" +
                                urlencode({"url": link, "format": "json"})))
    except FetchError as exc: raise FetchError(f"could not look that video up ({exc})") from exc
    except ValueError as exc: raise FetchError("could not read that video's details") from exc
    if not (owner := reply.get("author_url")): raise FetchError("could not tell whose video it is")
    return owner


def scrape(page: str) -> str:
    """The channel ID behind a channel page: an @handle, a /c/ or /user/ name, & so on."""
    try: body = get(page).decode("utf-8", "replace")
    except FetchError as exc:
        raise FetchError("no such channel" if "404" in str(exc) else str(exc)) from exc
    for pattern in PAGE_IDS:
        if found := pattern.search(body): return found[1]
    raise FetchError("could not find a channel on that page")


def locate(link: str) -> str:
    """The channel (or playlist) ID that a YouTube link leads to."""
    parts = urlsplit(link)
    host = re.sub(r"^(?:www|m|music)\.", "", (parts.hostname or "").lower())
    if host not in ("youtube.com", "youtu.be"): raise FetchError(f"not a YouTube link: {link}")
    query, path = parse_qs(parts.query), parts.path.rstrip("/")

    if found := query.get("channel_id") or query.get("playlist_id"): return found[0]
    if found := re.match(r"/channel/(UC[\w-]{22})", path): return found[1]
    if path == "/playlist" and query.get("list"): return query["list"][0]
    if host == "youtu.be": return locate(video_owner(path.strip("/")))
    if re.match(r"/(?:watch|shorts|live|embed)\b", path):
        vid = query.get("v", [""])[0] or path.split("/")[-1]
        return locate(video_owner(vid))

    steps = path.strip("/").split("/")
    if not steps[0]: raise FetchError("that is YouTube's front page, not a channel")
    return scrape("https://www.youtube.com/" +
                  "/".join(steps[:1] if steps[0].startswith("@") else steps[:2]))


def resolve(text: str) -> tuple[Channel, list[Video]]:
    """Whatever was pasted -- a link to a channel, video, playlist or feed, an @handle, or an ID
    -- -> the subscription it means, with a first fetch of its feed."""
    text = text.strip().strip("<>\"'")
    if CHANNEL_ID.fullmatch(text) or PLAYLIST_ID.fullmatch(text): sub = text
    elif " " in text: raise FetchError("that is not a link, an @handle or a channel ID")
    else:
        if re.fullmatch(r"@?[\w.-]+", text) and "youtu" not in text: text = "@" + text.lstrip("@")
        if text.startswith("@"): text = "https://www.youtube.com/" + text
        sub = locate(text if "://" in text else "https://" + text)
    title, videos = read_feed(sub)
    return Channel(sub, title), videos


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


def paste() -> str:
    for cmd in (["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"],
                ["xsel", "-ob"], ["pbpaste"]):
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            if done.returncode == 0: return done.stdout.strip()
        except (OSError, subprocess.SubprocessError): continue
    return ""


#---- layout ---------------------------------------------------------------------------------------

L_RULE, L_GAP, L_ITEM = range(3)


class Line(NamedTuple):
    """One line of a tab: a heading, a gap, or an item (a Video or a Channel)."""
    kind: int
    text: str = ""
    ref: object = None


@dataclass
class View:
    """One tab's lines, & where its items sit among them."""
    lines: list[Line] = field(default_factory=list)
    rows: list[int] = field(default_factory=list)  # the line of each item ...
    leads: list[int] = field(default_factory=list) # ... & the one to scroll to, to show its heading
    ids: list[str] = field(default_factory=list)

    def heading(self, text: str):
        if self.lines: self.lines.append(Line(L_GAP))
        self.lines.append(Line(L_RULE, text))

    def item(self, key: str, ref):
        under = bool(self.lines) and self.lines[-1].kind == L_RULE
        self.leads.append(len(self.lines) - under)
        self.rows.append(len(self.lines))
        self.ids.append(key)
        self.lines.append(Line(L_ITEM, ref=ref))

    def ref(self, index: int): return self.lines[self.rows[index]].ref


#---- app ------------------------------------------------------------------------------------------

@dataclass
class Pane:
    """Where the reader is within one tab."""
    sel: int = 0
    top: int = 0
    anchor: str = "" # the selected item, to find it again when the tab is rebuilt
    query: str = ""
    view: View | None = None
    shape: tuple = ()


@dataclass
class Status:
    """How one subscription's last fetch went, or the fetch still running."""
    error: str = ""
    tried: float = 0.0
    future: Future | None = None


@dataclass
class Round:
    """A batch of fetches, reported on once the last of them is in."""
    total: int
    found: int = 0


@dataclass
class Lookup:
    """The subscribe box: the link being typed, & the lookup of it once sent."""
    text: str = ""
    error: str = ""
    job: Future | None = None


@dataclass
class Ask:
    """A yes-or-no question, & what to do on a yes."""
    title: str
    lines: list[str]
    action: object
    alarm: bool = False


TABS = ("new", "all", "channels")

HELP = [
    ("↑ ↓   j k",   "move"),
    ("PgUp PgDn",   "scroll by screenful"),
    ("g G",         "first / last"),
    ("← →   h l",   "previous / next tab  (also Tab, ⇧Tab)"),
    ("1 … 4",       "jump straight to tab"),
    ("⏎   o",       "launch video, or list channel's videos"),
    ("space",       "toggle new / watched"),
    ("s",           "sort: by channel, or by date"),
    ("i",           "info: description, views, status of last fetch"),
    ("a",           "add subscription (from link)"),
    ("d",           "delete highlighted subscription (Channels tab)"),
    ("e",           f"edit subscription file: {URLS_FILE}"),
    ("/",           "filter;  Esc clears"),
    ("y",           "copy link to clipboard"),
    ("r",           "refresh feed"),
    ("W",           "mark all as watched"),
    ("^L",          "repaint screen"),
    ("q   Esc",     "back / quit"),
]


class App:
    def __init__(self, scr, urls: Urls, store: Store):
        self.scr, self.urls, self.store = scr, urls, store
        self.pool = ThreadPoolExecutor(WORKERS, thread_name_prefix="fetch")
        self.lookups = ThreadPoolExecutor(1, thread_name_prefix="lookup")
        self.status: dict[str, Status] = {}
        self.round: Round | None = None
        self.updated = self.due = 0.0     # when the last round ended, & when the next is due

        self.tab, self.feed = "new", ""   # the open tab, & the channel given a tab of its own
        self.panes: dict[str, Pane] = {}
        self.linger: set[str] = set()     # watched while on the New tab; kept there till it is left
        self.fresh = 0                    # bumped whenever the videos or subscriptions change
        self._sorted: tuple = (-1, [])
        self._tally: tuple = (-1, {})

        self.toast = self.typing = None
        self.adding: Lookup | None = None
        self.asking: Ask | None = None
        self.by_name = False              # the New & All tabs sorted by channel, then by date
        self.info: int | None = None      # how far the details box is scrolled, while it is open,
        self.info_rows = self.info_total = 1  # how many lines it shows, & of how many
        self.helping, self.running, self.saved = False, True, time.monotonic()

    # --- subscriptions --------------------------------------------------
    def name(self, sub: str) -> str:
        channel = self.urls.by_id.get(sub)
        return (channel and channel.name) or self.store.titles.get(sub) or sub

    def cutoff(self) -> float: return time.time() - MAX_AGE * 86400

    def everything(self) -> list[Video]:
        """Every video, newest first; sorted again only once the videos change."""
        if self._sorted[0] != self.fresh:
            self._sorted = (self.fresh, sorted(self.store.videos.values(),
                                               key=lambda v: -v.published))
        return self._sorted[1]

    def tally(self) -> dict[str, list[Video]]:
        """Each subscription's videos, newest first."""
        if self._tally[0] != self.fresh:
            table: dict[str, list[Video]] = {}
            for video in self.everything(): table.setdefault(video.channel, []).append(video)
            self._tally = (self.fresh, table)
        return self._tally[1]

    def sync(self):
        """Bring the fetches & remembered videos into line with the subscriptions file."""
        subs = self.urls.by_id
        for sub in [s for s in self.status if s not in subs]: del self.status[sub]
        if self.feed and self.feed not in subs: self.close_feed()
        self.store.prune(set(subs), self.cutoff())
        self.fresh += 1
        self.refresh([s for s in subs if s not in self.status])

    # --- fetching -------------------------------------------------------
    def refresh(self, subs: list[str] | None = None) -> int:
        """Fetch the given subscriptions, or all of them, in the background."""
        subs = [c.id for c in self.urls.channels] if subs is None else subs
        subs = [s for s in subs if not self.status.setdefault(s, Status()).future]
        for sub in subs: self.status[sub].future = self.pool.submit(read_feed, sub)
        if subs:
            if self.round: self.round.total += len(subs)
            else: self.round = Round(len(subs))
            self.due = time.monotonic() + REFRESH * 60
        return len(subs)

    def pending(self) -> int: return sum(st.future is not None for st in self.status.values())

    def failed(self) -> list[str]:
        return [sub for sub, st in self.status.items() if st.error and not st.future]

    def poll(self):
        """Collect finished fetches, & the lookup behind the subscribe box."""
        cutoff = self.cutoff()
        for sub, st in self.status.items():
            if st.future is None or not st.future.done(): continue
            try:
                title, videos = st.future.result()
                found = self.store.merge(sub, title, videos, cutoff)
                if self.round: self.round.found += found
                st.error = ""
            except FetchError as exc: st.error = str(exc)
            except Exception as exc: st.error = f"{type(exc).__name__}: {exc}"
            st.future, st.tried, self.fresh = None, time.time(), self.fresh + 1

        if self.round and not self.pending(): self.finish()
        if self.adding and self.adding.job and self.adding.job.done(): self.looked_up()

    def finish(self):
        """A round of fetches is over: say how it went, & keep what came in."""
        found, failed = self.round.found, len(self.failed())
        self.round, self.updated = None, time.time()
        news = f"{count(found, 'new video')}" if found else "nothing new"
        if failed: self.notify(f"{news} · {count(failed, 'channel')} failed to fetch "
                               f"(see Channels; r tries again)", pair(C_WARN))
        else: self.notify(news)
        self.autosave(force=True)

    def autosave(self, force: bool = False):
        if self.store.dirty and (force or time.monotonic() - self.saved > 3):
            try: self.store.save()
            except OSError as exc:
                self.notify(f"could not save {self.store.path}: {exc.strerror or exc}",
                            pair(C_WARN))
            self.saved = time.monotonic()

    # --- tabs -----------------------------------------------------------
    def tabs(self) -> list[str]: return [*TABS, "feed"] if self.feed else list(TABS)

    @property
    def pane(self) -> Pane:
        return self.panes.setdefault(f"feed:{self.feed}" if self.tab == "feed" else self.tab,
                                     Pane())

    def go(self, tab: str):
        """Open a tab; leaving the New tab lets go of the videos watched while on it."""
        if self.tab == "new" and tab != "new" and self.linger:
            self.linger.clear()
            self.fresh += 1
        self.tab = tab

    def switch(self, delta: int):
        tabs = self.tabs()
        self.go(tabs[(tabs.index(self.tab) + delta) % len(tabs)])

    def open_feed(self, sub: str):
        self.feed = sub
        self.go("feed")

    def close_feed(self):
        if self.tab == "feed": self.go("channels")
        self.feed = ""

    # --- layout ---------------------------------------------------------
    def videos(self) -> list[Video]:
        """The open tab's videos, newest first."""
        if self.tab == "new": return [v for v in self.everything() if v.new or v.id in self.linger]
        if self.tab == "feed": return self.tally().get(self.feed, [])
        return self.everything()

    def build(self, query: str) -> View:
        view, needle = View(), query.lower()
        if self.tab == "channels":
            group = ""
            for channel in self.urls.channels:
                if needle not in self.name(channel.id).lower(): continue
                if channel.group != group:
                    view.heading(channel.group or "Other")
                    group = channel.group
                view.item(channel.id, channel)
            return view

        videos = [v for v in self.videos() if not needle or needle in v.title.lower()
                  or needle in self.name(v.channel).lower()]
        if self.by_channel(): # stable, so each channel's videos stay newest first
            videos.sort(key=lambda v: self.name(v.channel).casefold())
        for video in videos: view.item(video.id, video)
        return view

    def by_channel(self) -> bool: return self.by_name and self.tab in ("new", "all")

    def resort(self):
        if self.tab not in ("new", "all"): return
        self.by_name = not self.by_name
        self.notify("sorted by channel, then date" if self.by_name else "sorted by date",
                    pair(C_DIM))

    def view(self) -> View:
        """The open tab, rebuilt only when what it shows has changed."""
        pane = self.pane
        shape = (self.tab, self.feed, self.fresh, pane.query, self.by_channel())
        if pane.view is None or pane.shape != shape:
            pane.view, pane.shape = self.build(pane.query), shape
            ids = pane.view.ids
            pane.sel = (ids.index(pane.anchor) if pane.anchor in ids
                        else min(pane.sel, max(0, len(ids) - 1)))
            pane.anchor = ids[pane.sel] if ids else ""
        return pane.view

    def column(self, w: int) -> tuple[int, int]:
        """Width & left edge of the list, centred in the panel when there is room."""
        avail = max(10, w - 6)
        width = min(LIST_WIDTH, avail)
        return width, 3 + (avail - width) // 2

    def page_rows(self) -> int: return max(1, self.scr.getmaxyx()[0] - 4)

    def follow(self):
        """Scroll the minimum needed to bring the selection (& its heading, if first) into view."""
        pane, rows = self.pane, self.page_rows()
        view = pane.view
        if not view or not view.ids:
            pane.top = 0
            return
        line, lead = view.rows[pane.sel], view.leads[pane.sel]
        if line < pane.top or lead < pane.top <= line: pane.top = lead
        elif line >= pane.top + rows: pane.top = line - rows + 1
        pane.top = max(0, min(pane.top, len(view.lines) - rows))

    def select(self, index: int):
        view, pane = self.view(), self.pane
        if not view.ids: return
        pane.sel = max(0, min(len(view.ids) - 1, index))
        pane.anchor = view.ids[pane.sel]
        self.follow()

    def move(self, delta: int): self.select(self.pane.sel + delta)

    # --- actions --------------------------------------------------------
    def selected(self):
        view = self.view()
        return view.ref(self.pane.sel) if view.ids else None

    def target(self) -> Channel | None:
        """The channel that d acts on: the one selected, or the one with a tab of its own."""
        item = self.selected()
        if self.tab == "feed": sub = self.feed
        else: sub = item.id if isinstance(item, Channel) else ""
        return self.urls.by_id.get(sub)

    def enter(self):
        item = self.selected()
        if isinstance(item, Channel): self.open_feed(item.id)
        elif isinstance(item, Video): self.watch(item)

    def watch(self, video: Video):
        if problem := open_url(video.url):
            self.notify(problem, pair(C_WARN))
            return
        self.mark(video, False)
        self.notify(f"opened “{video.title}”")

    def mark(self, video: Video, new: bool):
        if video.new != new:
            video.new, self.store.dirty, self.fresh = new, True, self.fresh + 1
        if self.tab == "new": self.linger.add(video.id)

    def toggle(self):
        if isinstance(video := self.selected(), Video):
            self.mark(video, not video.new)
            self.move(1)

    def ask_mark_all(self):
        if self.tab == "channels": return
        view = self.view()
        videos = [video for i in range(len(view.ids)) if (video := view.ref(i)).new]
        if not videos:
            self.notify("nothing new here", pair(C_DIM))
            return
        self.asking = Ask("Mark watched", [f"Mark {count(len(videos), 'video')} watched?"],
                          lambda: self.mark_all(videos))

    def mark_all(self, videos: list[Video]):
        for video in videos: self.mark(video, False)
        self.notify(f"marked {count(len(videos), 'video')} watched")

    def copy(self):
        item = self.selected()
        if not item:
            self.notify("nothing to copy", pair(C_WARN))
            return
        url = item.url if isinstance(item, Video) else page_url(item.id)
        problem = clipboard(url)
        self.notify(problem or f"copied {url}", pair(C_WARN) if problem else None)

    def check(self):
        started = self.refresh()
        self.notify(f"checking {count(started, 'channel')}" if started
                    else "still checking" if self.round else "no channels to check")

    # --- subscribing ----------------------------------------------------
    def start_adding(self):
        """Open the subscribe box, with the clipboard's link already in it if it has one."""
        held = paste()
        self.adding = Lookup(held if len(held) < 300 and LINKISH.search(held) else "")

    def send_lookup(self):
        box = self.adding
        if not box.text.strip(): box.error = "paste a link first"
        else: box.error, box.job = "", self.lookups.submit(resolve, box.text)

    def looked_up(self):
        box = self.adding
        try: channel, videos = box.job.result()
        except FetchError as exc: box.error = str(exc)
        except Exception as exc: box.error = f"{type(exc).__name__}: {exc}"
        else: self.subscribe(channel, videos)
        box.job = None

    def subscribe(self, channel: Channel, videos: list[Video]):
        box = self.adding
        if channel.id in self.urls.by_id:
            box.error = f"already subscribed to {self.name(channel.id)}"
            return
        item = self.selected()
        after = item.id if self.tab == "channels" and isinstance(item, Channel) else ""
        try: self.urls.add(channel, after)
        except OSError as exc:
            box.error = f"could not save {self.urls.path}: {exc.strerror or exc}"
            return

        self.adding = None
        self.status[channel.id] = Status(tried=time.time())
        found = self.store.merge(channel.id, channel.name, videos, self.cutoff())
        self.sync() # the file was read again; anything else new in it is fetched too
        if self.tab == "channels": self.pane.anchor = channel.id
        self.notify(f"subscribed to {channel.name} · {count(found, 'video')} "
                    f"from the last {MAX_AGE} days")

    def ask_unsubscribe(self):
        if self.tab not in ("channels", "feed"):
            self.notify("d unsubscribes on the Channels tab", pair(C_DIM))
            return
        if not (channel := self.target()): return
        name, videos = self.name(channel.id), len(self.tally().get(channel.id, []))
        self.asking = Ask("Unsubscribe", [f"Unsubscribe from {name}?",
                                          f"Its {count(videos, 'video')} will be forgotten too."
                                          if videos else ""],
                          lambda: self.unsubscribe(channel.id, name), alarm=True)

    def unsubscribe(self, sub: str, name: str):
        try: self.urls.remove(sub)
        except OSError as exc:
            self.notify(f"could not save {self.urls.path}: {exc.strerror or exc}", pair(C_WARN))
            return
        self.sync()
        self.notify(f"unsubscribed from {name}")

    def edit_urls(self):
        """Hand the terminal to $EDITOR for the subscriptions file, then take in the changes."""
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "sensible-editor"
        curses.def_prog_mode()
        curses.endwin()
        try: subprocess.call(shlex.split(editor) + [self.urls.path])
        except OSError as exc: self.notify(f"{editor}: {exc.strerror or exc}", pair(C_WARN))
        finally:
            curses.reset_prog_mode()
            try: curses.curs_set(0)
            except curses.error: pass
            curses.flushinp()
            self.scr.clear()

        try: self.urls.load()
        except OSError as exc:
            self.notify(f"could not read {self.urls.path}: {exc.strerror or exc}", pair(C_WARN))
            return
        self.sync()
        if self.urls.problems: self.notify(self.urls.problems[0], pair(C_WARN))
        elif not self.toast: self.notify(count(len(self.urls.channels), "subscription"))

    # --- state ----------------------------------------------------------
    def notify(self, text, attr=None):
        self.toast = (text, pair(C_HEAD) if attr is None else attr,
                      time.monotonic() + TOAST_SECONDS)

    def live_toast(self):
        if self.toast and time.monotonic() >= self.toast[2]: self.toast = None
        return self.toast

    def spinner(self) -> str: return SPINNER[int(time.monotonic() * 12) % len(SPINNER)]

    # --- drawing --------------------------------------------------------
    def segments(self, y, x, room, segs):
        """A line of differently styled runs, clipped to room columns."""
        for text, attr in segs:
            if room <= 0: break
            put(self.scr, y, x, text, attr, room)
            x, room = x + dwidth(text), room - dwidth(text)

    def draw_topbar(self, w):
        attr = pair(C_BAR, True)
        put(self.scr, 0, 0, " " * w, attr)
        failed = len(self.failed())
        alarm = f" ✗ {failed} failed " if failed else ""
        if self.round:
            done = self.round.total - self.pending()
            status = f"{self.spinner()} fetching {done}/{self.round.total} "
        elif self.updated: status = f"updated {datetime.fromtimestamp(self.updated):%H:%M} "
        else: status = ""

        right = dwidth(status) + dwidth(alarm)
        room = max(0, w - right - 2)
        for left in (f"{APP_NAME} {VERSION}  ·  YouTube subscriptions", f"{APP_NAME} {VERSION}",
                     APP_NAME, ""):
            if dwidth(left) <= room: break
        put(self.scr, 0, 1, left, attr, room)
        put(self.scr, 0, max(0, w - right), status, attr)
        if alarm: put(self.scr, 0, w - dwidth(alarm), alarm, pair(C_ALARM, True))

    def tab_label(self, tab: str) -> str:
        if tab == "new": return f"New {sum(v.new for v in self.store.videos.values())}"
        if tab == "all": return f"All {len(self.store.videos)}"
        if tab == "channels": return f"Channels {len(self.urls.channels)}"
        return f"▸ {clip(self.name(self.feed), 24)} {len(self.tally().get(self.feed, []))}"

    def tab_mark(self, tab: str) -> tuple[str, int]:
        """A spinner beside the Channels tab while fetching; failures are in the top bar."""
        if tab == "channels" and self.round: return self.spinner(), pair(C_HEAD)
        if tab == "feed" and self.feed in self.failed(): return "✗", pair(C_WARN, True)
        return "", 0

    def draw_tabs(self, y, w):
        x = 2
        for tab in self.tabs():
            label = f" {self.tab_label(tab)} "
            attr = pair(C_SEL, True) if tab == self.tab else pair(C_DIM)
            put(self.scr, y, x, label, attr, max(0, w - 3 - x))
            x += dwidth(label)
            mark, mark_attr = self.tab_mark(tab)
            if mark:
                put(self.scr, y, x + 1, mark, mark_attr, max(0, w - 4 - x))
                x += dwidth(mark) + 1
            x += 1

    def draw_panel(self, top, bottom, w):
        border = pair(C_BORDER)
        put(self.scr, top, 0, "╭" + "─" * (w - 2) + "╮", border)
        put(self.scr, bottom, 0, "╰" + "─" * (w - 2) + "╯", border)
        for y in range(top + 1, bottom):
            put(self.scr, y, 0, "│", border)
            put(self.scr, y, w - 1, "│", border)
        self.draw_tabs(top, w)

        rows, view = bottom - top - 1, self.view()
        if not view.ids: return self.draw_notice(top, rows, w, self.emptiness())
        self.follow()
        self.draw_list(top + 1, rows, w, view)
        self.draw_scrollbar(top + 1, w - 1, rows, len(view.lines), self.pane.top)

    def emptiness(self) -> list[tuple[str, int]]:
        """What an empty tab says instead."""
        dim, query = pair(C_DIM), self.pane.query
        if not self.urls.channels:
            return [("Feed me!", pair(C_HEAD, True)), ("", 0),
                    ("no subscriptions yet: press a, & paste a link to a YouTube channel", dim)]
        if query: return [(f"nothing here matches “{query}”", dim), ("", 0),
                          ("esc clears the filter", dim)]
        if self.round and self.tab != "channels":
            return [(f"{self.spinner()}  checking {count(self.round.total, 'channel')}…", dim)]
        if self.tab == "feed":
            if self.feed in self.failed():
                return [(f"could not fetch {self.name(self.feed)}", pair(C_WARN)),
                        (self.status[self.feed].error, dim), ("", 0), ("r tries again", dim)]
            return [(f"nothing from {self.name(self.feed)} in the last {MAX_AGE} days", dim)]
        if self.tab == "new":
            return [("Nothing new.", pair(C_HEAD, True)), ("", 0),
                    ("r checks again  ·  a subscribes to another channel", dim)]
        return [("no videos yet", dim), ("", 0), ("r checks again", dim)]

    def draw_notice(self, top, rows, w, lines):
        for i, (text, attr) in enumerate(lines):
            y = top + 1 + max(0, (rows - len(lines)) // 2) + i
            if y < top + 1 + rows: put(self.scr, y, 1, fit(text, w - 2, centre=True), attr, w - 2)

    def name_width(self, width: int) -> int:
        longest = max((dwidth(self.name(c.id)) for c in self.urls.channels), default=8)
        return max(6, min(NAME_WIDTH, longest, width // 3))

    def draw_list(self, y0, rows, w, view):
        width, x0 = self.column(w)
        pane, names = self.pane, 0 if self.tab == "feed" else self.name_width(width)
        chosen = view.rows[pane.sel]
        for offset in range(rows):
            index = pane.top + offset
            if index >= len(view.lines): break
            y, line = y0 + offset, view.lines[index]
            if line.kind == L_RULE: self.draw_rule(y, x0, width, line.text)
            elif line.kind == L_ITEM:
                if index == chosen: put(self.scr, y, x0 - 1, " " * (width + 2), pair(C_SEL))
                draw = self.draw_video if isinstance(line.ref, Video) else self.draw_channel
                draw(y, x0, width, names, line.ref, index == chosen)

    def draw_rule(self, y, x, width, text):
        """A heading set into a horizontal rule."""
        label = f" {clip(text, max(1, width - 6))} "
        put(self.scr, y, x, "──", pair(C_BORDER))
        put(self.scr, y, x + 2, label, pair(C_HEAD, True))
        put(self.scr, y, x + 2 + dwidth(label), "─" * max(0, width - 2 - dwidth(label)),
            pair(C_BORDER))

    def draw_video(self, y, x, width, names, video, chosen):
        end = x + width
        if chosen: dot = when = name = title = pair(C_SEL, True)
        elif video.new:
            dot, when, name = pair(C_HEAD, True), pair(C_DIM), pair(C_NAME)
            title = pair(C_NEW, True)
        else: dot, when, name, title = 0, pair(C_DIM), pair(C_SEEN), pair(C_SEEN)

        put(self.scr, y, x, "●" if video.new else " ", dot)
        put(self.scr, y, x + 2, short_date(video.published), when)
        x += 2 + DATE_WIDTH + 2
        if names:
            put(self.scr, y, x, fit(self.name(video.channel), names), name)
            x += names + 2
        put(self.scr, y, x, fit(video.title, end - x), title)

    def draw_channel(self, y, x, width, names, channel, chosen):
        end, dim = x + width, pair(C_DIM)
        st, videos = self.status.get(channel.id, Status()), self.tally().get(channel.id, [])
        if st.future: glyph, glyph_attr = self.spinner(), pair(C_HEAD)
        elif st.error: glyph, glyph_attr = "✗", pair(C_WARN, True)
        elif channel.id in self.store.fetched: glyph, glyph_attr = "✓", pair(C_BORDER)
        else: glyph, glyph_attr = "·", dim

        new = sum(v.new for v in videos) # what earlier fetches left, failed or not
        info = [(f"{new} new", pair(C_HEAD, True))] if new else []
        info.append((count(len(videos), "video"), dim))
        if videos: info.append((f"latest {ago(time.time() - videos[0].published)}", dim))
        segs = [part for n, seg in enumerate(info)
                for part in ([(" · ", dim)] if n else []) + [seg]]
        # a failure's status code, right-aligned; the whole message is in the details box (i)
        code = brief(st.error) if st.error and not st.future else ""

        name_attr, code_attr = pair(C_NAME), pair(C_WARN)
        if chosen:
            glyph_attr = name_attr = code_attr = pair(C_SEL, True)
            segs = [(text, pair(C_SEL, True)) for text, _ in segs]
        put(self.scr, y, x, glyph, glyph_attr)
        put(self.scr, y, x + 2, fit(self.name(channel.id), names), name_attr)
        room = end - x - 4 - names - (dwidth(code) + 2 if code else 0)
        self.segments(y, x + 4 + names, room, segs)
        if code: put(self.scr, y, end - dwidth(code), code, code_attr)

    def draw_scrollbar(self, y0, x, rows, total, top):
        """A scrollbar down a border, for rows lines in view of total, from the top-th."""
        if total <= rows: return
        size = max(1, rows * rows // total)
        start = (top * (rows - size)) // max(1, total - rows)
        for row in range(rows):
            glyph = "█" if start <= row < start + size else "│"
            put(self.scr, y0 + row, x, glyph, pair(C_BORDER))

    def details(self, width: int) -> list[list[tuple[str, int]]]:
        """Everything known about the selection, as lines of (text, attr) runs, wrapped to width."""
        item, dim, now = self.selected(), pair(C_DIM), time.time()

        def para(text, attr): return [[(line, attr)] for line in wrap(text, width)]

        if isinstance(item, Video):
            when = datetime.fromtimestamp(item.published)
            meta = [(f"{when:%A} {when.day} {when:%B %Y}, {when:%H:%M}", dim)]
            if item.views: meta.append((f"  ·  {item.views:,} views", dim))
            meta.append(("  ·  new", pair(C_HEAD, True)) if item.new else ("  ·  watched", dim))
            return (para(item.title, curses.A_BOLD)
                    + [[(self.name(item.channel), pair(C_NAME))], meta, []]
                    + (para(item.description, 0) or [[("no description", dim)]])
                    + [[], [(item.url, dim)]])

        if not isinstance(item, Channel): return []
        st, videos = self.status.get(item.id, Status()), self.tally().get(item.id, [])
        kind = "channel" if CHANNEL_ID.fullmatch(item.id) else "playlist"
        good = self.store.fetched.get(item.id)
        lines = para(self.name(item.id), curses.A_BOLD)
        lines += [[(kind + (f" in {item.group}" if item.group else ""), pair(C_NAME))], []]

        if st.future: lines.append([(f"{self.spinner()} fetching…", pair(C_HEAD))])
        elif st.error:
            lines.append([("✗ could not fetch", pair(C_WARN, True)),
                          (f"  ·  {ago(now - st.tried)}", dim)])
            lines += para(st.error, pair(C_WARN))
            lines += para(f"{'Last fetched fine ' + ago(now - good) if good else 'Never fetched'}. "
                          f"YouTube's feeds fail now & then; r tries again.", dim)
        elif good:
            lines.append([("✓ ", pair(C_BORDER, True)), (f"fetched {ago(now - good)}", dim)])
        lines.append([])

        if videos:
            new = sum(v.new for v in videos)
            tally = [(f"{count(len(videos), 'video')} in the last {MAX_AGE} days", 0)]
            if new: tally += [("  ·  ", dim), (f"{new} new", pair(C_HEAD, True))]
            lines += [tally, []]
            for video in videos[:6]:
                lines.append([(short_date(video.published) + "  ", dim),
                              (fit(video.title, width - DATE_WIDTH - 2),
                               pair(C_NEW, True) if video.new else pair(C_SEEN))])
        else: lines.append([(f"nothing in the last {MAX_AGE} days", dim)])
        return lines + [[], [(page_url(item.id), dim)], [(feed_url(item.id), dim)]]

    def footnote(self) -> str:
        item = self.selected()
        if isinstance(item, Video): return item.url
        return feed_url(item.id) if isinstance(item, Channel) else ""

    def draw_footer(self, y, w):
        """Toast, else the filter, else the selection's link; hints on the right."""
        if self.typing is not None:
            put(self.scr, y, 1, fit(f"/{self.typing}▏", w - 2), pair(C_HEAD), w - 2)
            return
        toast = self.live_toast()
        if toast: left, attr = f"● {toast[0]}", toast[1]
        elif self.pane.query: left, attr = f"/{self.pane.query}  ·  esc clears", pair(C_HEAD)
        else: left, attr = self.footnote(), pair(C_DIM)

        tiers = HINTS["videos" if self.tab in ("new", "all") else self.tab]
        hints = next((h for h in tiers if dwidth(h) + 12 <= w), tiers[-1])
        x = max(1, w - dwidth(hints) - 2) # slack: ⏎ is ambiguous-width
        if toast and dwidth(left) > x - 3:
            x = w # a long toast keeps the footer to itself
        else:
            put(self.scr, y, x, hints, pair(C_DIM), w - 1 - x)
        if toast or x - 3 > 8:
            put(self.scr, y, 1, fit(left, x - 3), attr, x - 3)

    def draw_box(self, title, body, width, alarm=False, centre=False) -> tuple[int, int, int, int]:
        """A rounded box over the panel; body is lines of (text, attr) runs. Returns where it went:
        its top, left, width & height."""
        h, w = self.scr.getmaxyx()
        bw, bh = max(24, min(w - 4, width)), min(h - 2, len(body) + 2)
        y0, x0 = max(0, (h - bh) // 2), max(0, (w - bw) // 2)
        border = pair(C_WARN if alarm else C_BORDER)
        put(self.scr, y0, x0, "╭" + "─" * (bw - 2) + "╮", border)
        put(self.scr, y0 + bh - 1, x0, "╰" + "─" * (bw - 2) + "╯", border)
        put(self.scr, y0, x0 + 2, f" {title} ", pair(C_ALARM if alarm else C_SEL, True))

        for i, segs in enumerate(body[:bh - 2]):
            y, room = y0 + 1 + i, bw - 6
            put(self.scr, y, x0, "│" + " " * (bw - 2) + "│", border)
            span = sum(dwidth(text) for text, _ in segs)
            self.segments(y, x0 + 3 + (max(0, room - span) // 2 if centre else 0), room, segs)
        return y0, x0, bw, bh

    def draw_info(self, w):
        """The selection's details in a box of their own, scrolled down self.info lines."""
        h, item = self.scr.getmaxyx()[0], self.selected()
        width = max(30, min(INFO_WIDTH, w - 8))
        if not (lines := self.details(width - 6)):
            self.info = None # the selection has gone
            return
        rows = max(1, min(h - 6, len(lines)))
        self.info_rows, self.info_total = rows, len(lines)
        self.info = max(0, min(self.info, len(lines) - rows))

        title = "Video" if isinstance(item, Video) else "Channel"
        y0, x0, bw, bh = self.draw_box(title, [[]] + lines[self.info:self.info + rows] + [[]],
                                       width)
        self.draw_scrollbar(y0 + 2, x0 + bw - 1, rows, len(lines), self.info)
        for hints in (f" ↑↓ scroll · ⏎ {'watch' if title == 'Video' else 'videos'} · y copy link "
                      f"· esc close ", " esc close ", ""):
            if dwidth(hints) + 6 <= bw: break
        put(self.scr, y0 + bh - 1, x0 + bw - 3 - dwidth(hints), hints, pair(C_DIM))

    def draw_help(self, w):
        pad = max(dwidth(k) for k, _ in HELP)
        body = [[]] + [[(k + " " * (pad - dwidth(k)), pair(C_HEAD)), (f"   {what}", 0)]
                       for k, what in HELP]
        body += [[], [("any key to dismiss", pair(C_DIM))]]
        self.draw_box(f"{APP_NAME} keys", body, pad + max(dwidth(t) for _, t in HELP) + 10)

    def draw_adding(self, w):
        box, dim = self.adding, pair(C_DIM)
        width = min(78, w - 8)
        room = max(10, width - 6)
        typed = fit(box.text + ("" if box.job else "▏"), room - 2, tail=True)
        if box.job: status = [[(f"{self.spinner()}  looking it up…", pair(C_HEAD))]]
        elif box.error: status = [[(text, pair(C_WARN))] for text in wrap(box.error, room)]
        else: status = [[("⏎ subscribe  ·  esc cancel", dim)]]
        body = [[], [("Paste a link to a channel, a video or a playlist,", 0)],
                [("or type a channel's @handle.", 0)], [],
                [("› ", pair(C_HEAD, True)), (typed, curses.A_BOLD | curses.A_UNDERLINE)], []]
        self.draw_box("Subscribe", body + status, width)

    def draw_asking(self, w):
        ask = self.asking
        body = [[]] + [[(text, curses.A_BOLD if n == 0 else pair(C_DIM))]
                       for n, text in enumerate(ask.lines) if text]
        body += [[], [("[y] yes", pair(C_ALARM if ask.alarm else C_SEL, True)), ("     ", 0),
                      ("[n] no", pair(C_DIM))]]
        self.draw_box(ask.title, body, max(dwidth(t) for t in ask.lines) + 12, ask.alarm, True)

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        if h < 10 or w < 44: put(self.scr, 0, 0, "terminal too small")
        else:
            self.view()
            self.draw_topbar(w)
            self.draw_panel(1, h - 2, w)
            self.draw_footer(h - 1, w)
            if self.helping: self.draw_help(w)
            elif self.adding: self.draw_adding(w)
            elif self.asking: self.draw_asking(w)
            elif self.info is not None: self.draw_info(w)
        self.scr.noutrefresh()
        curses.doupdate()

    # --- input ----------------------------------------------------------
    def type_filter(self, key, ch):
        """Keys while typing a filter, which applies as it is typed."""
        if key in (10, 13):
            self.typing = None
            return
        if key == 27: self.typing = ""
        elif key in (curses.KEY_BACKSPACE, 127, 8): self.typing = self.typing[:-1]
        elif isinstance(ch, str) and ch.isprintable(): self.typing += ch
        self.pane.query = self.typing
        if key == 27: self.typing = None

    def type_link(self, key, ch):
        """Keys in the subscribe box."""
        box = self.adding
        if key == 27: self.adding = None # a lookup still running is left to finish, unheard
        elif box.job: return
        elif key in (10, 13): self.send_lookup()
        elif key in (curses.KEY_BACKSPACE, 127, 8): box.text, box.error = box.text[:-1], ""
        elif key == 21: box.text, box.error = "", "" # ^U
        elif isinstance(ch, str) and ch.isprintable(): box.text, box.error = box.text + ch, ""

    def in_info(self, key):
        """Keys while the details box is open."""
        page = max(1, self.info_rows - 1)
        if key in (curses.KEY_DOWN, ord("j")): self.info += 1
        elif key in (curses.KEY_UP, ord("k")): self.info -= 1
        elif key in (curses.KEY_NPAGE, ord(" "), 6): self.info += page
        elif key in (curses.KEY_PPAGE, 2): self.info -= page
        elif key in (curses.KEY_HOME, ord("g")): self.info = 0
        elif key in (curses.KEY_END, ord("G")): self.info = self.info_total
        elif key == ord("y"): self.copy()
        elif key in (10, 13, ord("o")):
            self.info = None
            self.enter()
        elif key in (ord("i"), ord("q"), 27): self.info = None
        if self.info is not None:
            self.info = max(0, min(self.info, self.info_total - self.info_rows))

    def answer(self, key):
        ask = self.asking
        if key in (ord("y"), ord("Y")):
            self.asking = None
            ask.action()
        elif key in (ord("n"), ord("N"), ord("q"), 27, 10, 13):
            self.asking = None
            self.notify("cancelled", pair(C_DIM))

    def handle(self, key, ch):
        if self.helping:
            self.helping = False
            return
        if self.asking: return self.answer(key)
        if self.adding: return self.type_link(key, ch)
        if self.info is not None: return self.in_info(key)
        if self.typing is not None: return self.type_filter(key, ch)

        pane, rows = self.pane, self.page_rows()
        if key == 27 and pane.query:
            pane.query = ""
            self.notify("filter cleared", pair(C_DIM))
        elif key in (ord("q"), 27) and self.tab == "feed": self.close_feed()
        elif key in (ord("q"), 27): self.running = False
        elif key in (curses.KEY_DOWN, ord("j")): self.move(1)
        elif key in (curses.KEY_UP, ord("k")): self.move(-1)
        elif key in (curses.KEY_NPAGE, 6): self.move(rows - 1)
        elif key in (curses.KEY_PPAGE, 2): self.move(1 - rows)
        elif key == 4: self.move(rows // 2)
        elif key == 21: self.move(-(rows // 2))
        elif key in (curses.KEY_HOME, ord("g")): self.select(0)
        elif key in (curses.KEY_END, ord("G")): self.select(len(self.view().ids) - 1)
        elif key in (curses.KEY_RIGHT, ord("l"), 9): self.switch(1)
        elif key in (curses.KEY_LEFT, ord("h"), curses.KEY_BTAB): self.switch(-1)
        elif ord("1") <= key <= ord("9"):
            if (index := key - ord("1")) < len(tabs := self.tabs()): self.go(tabs[index])
        elif key in (10, 13, ord("o")): self.enter()
        elif key == ord(" "): self.toggle()
        elif key == ord("W"): self.ask_mark_all()
        elif key == ord("s"): self.resort()
        elif key == ord("i") and self.selected(): self.info = 0
        elif key == ord("a"): self.start_adding()
        elif key == ord("d"): self.ask_unsubscribe()
        elif key == ord("e"): self.edit_urls()
        elif key == ord("/"): self.typing = pane.query
        elif key == ord("y"): self.copy()
        elif key == ord("r"): self.check()
        elif key == ord("?"): self.helping = True
        elif key == 12: self.scr.redrawwin() # ^L, for a screen left dirty by something else

    def read(self) -> tuple[int, object] | None:
        """The next key, as its code & the character typed, or None once the timeout runs out."""
        try: ch = self.scr.get_wch()
        except curses.error: return None
        return (ord(ch) if isinstance(ch, str) else ch), ch

    def run(self):
        self.sync() # which starts every channel fetching
        try:
            while self.running:
                self.poll()
                self.autosave()
                if REFRESH and not self.round and time.monotonic() >= self.due: self.refresh()
                self.draw()
                busy = self.round or self.adding and self.adding.job
                self.scr.timeout(80 if busy else 200 if self.toast else 30_000)
                got = self.read()
                self.scr.timeout(0)
                while got and self.running: # all the keys already waiting (a held ↓, say), then
                    key, ch = got           # one repaint, so that the screen never falls behind
                    if key == curses.KEY_RESIZE: self.scr.redrawwin()
                    else: self.handle(key, ch)
                    got = self.read()
        finally:
            self.autosave(force=True)
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.lookups.shutdown(wait=False, cancel_futures=True)


#---- entry ----------------------------------------------------------------------------------------

def setup() -> tuple[Urls, Store, list[str]]:
    """Load the subscriptions & the state, & say what went wrong with either."""
    urls, store = Urls(os.path.expanduser(URLS_FILE)), Store(os.path.expanduser(STATE_FILE))
    problem = store.load()
    urls.load()
    return urls, store, ([problem] if problem else []) + urls.problems


def main() -> int:
    if sys.argv[1:]:
        sys.exit(f"twoey takes no arguments; see the config block in "
                 f"{os.path.basename(__file__)}")
    if not sys.stdout.isatty(): sys.exit("twoey needs an interactive terminal")

    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    urls, store, problems = setup()

    def reader(scr):
        init_colors()
        try: curses.curs_set(0)
        except curses.error: pass
        app = App(scr, urls, store)
        if problems: app.notify(problems[0], pair(C_WARN))
        app.run()

    curses.wrapper(reader)
    return 0


if __name__ == "__main__":
    try: code = main()
    except KeyboardInterrupt: code = 130
    sys.stdout.flush()
    os._exit(code) # rather than wait on any fetch still stuck in a slow request
