#!/usr/bin/env python3
#---------------------------------------------------------------------------------------------------
# Appa Yip Yip!
#---------------------------------------------------------------------------------------------------
#
# Copies combination of random & recent music tracks from ~/Music onto USB drive (automatically
# detected), replacing all files on the drive; this is to keep a fresh rotating sample of a large
# music library available for playback on the go. Having been made for the Toyota Yaris sound-
# system, the script groups files into directories of 255 each, as the sound-system cannot read
# larger directories. Files matching any regex patterns in ~/Music/.appa-nope-nope are ignored.
#
# See additional configuration options below.
#
#---------------------------------------------------------------------------------------------------

import curses
import locale
import os
import random
import re
import shlex
import shutil
import subprocess
import struct
import time
from dataclasses import dataclass, field


#---- config ---------------------------------------------------------------------------------------

APP_NAME  = "Appa Yip-Yip!"
VERSION   = "0.2.0"
MUSIC_DIR = "~/Music"
NOPE_FILE = "~/Music/.appa-nope-nope"

N_RECENT  = 120
N_RANDOM  = 280
STEP      = 10

TOAST_SECONDS = 2.0

RUST, AMBER, AQUA, GREY, INK = "b4603e", "ffbe62", "7fffd4", "6b6b6b", "1c1c1c"
C_BAR, C_BORDER, C_TAB, C_TAB_OFF, C_SEL, C_DEAD, C_DIM, C_ACCENT, C_WARN, C_ARTIST = range(1, 11)

CHUNK = 255
FAT_INVALID = str.maketrans({c: "_" for c in '"*/\\:<>?|'})

TABS = ("recent", "random")
STAMP_W = 4
HINTS = {"recent": "x exclude · [] count · B edit blacklist · y yip-yip! · q quit",
         "random": "x redraw · s sort · [] count · R regen · B edit blacklist · y yip-yip! · q quit"}


#---- colour ---------------------------------------------------------------------------------------

_slots: dict[str, int] = {}


def _nearest256(r: int, g: int, b: int) -> int:
    levels = (0, 95, 135, 175, 215, 255)
    best = (1 << 30, 7)
    for i in range(16, 232):
        j = i - 16
        cr, cg, cb = levels[j // 36], levels[(j // 6) % 6], levels[j % 6]
        d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if d < best[0]:
            best = (d, i)
    for i in range(232, 256):
        v = 8 + (i - 232) * 10
        d = (r - v) ** 2 + (g - v) ** 2 + (b - v) ** 2
        if d < best[0]:
            best = (d, i)
    return best[1]


def ink(hex_: str) -> int:
    """Colour index for a hex triple: exact where the terminal allows, else nearest xterm-256."""
    if hex_ not in _slots:
        r, g, b = (int(hex_[i:i + 2], 16) for i in (0, 2, 4))
        if curses.can_change_color() and curses.COLORS >= 256:
            idx = 200 + len(_slots)
            curses.init_color(idx, *(round(v * 1000 / 255) for v in (r, g, b)))
        else:
            idx = _nearest256(r, g, b)
        _slots[hex_] = idx
    return _slots[hex_]


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    rust, amber, aqua, grey, ink_ = (ink(c) for c in (RUST, AMBER, AQUA, GREY, INK))
    curses.init_pair(C_BAR, ink_, rust)
    curses.init_pair(C_BORDER, rust, -1)
    curses.init_pair(C_TAB, ink_, amber)
    curses.init_pair(C_TAB_OFF, grey, -1)
    curses.init_pair(C_SEL, ink_, amber)
    curses.init_pair(C_DEAD, grey, -1)
    curses.init_pair(C_DIM, grey, -1)
    curses.init_pair(C_ACCENT, aqua, -1)
    curses.init_pair(C_WARN, rust, -1)
    curses.init_pair(C_ARTIST, amber, -1)


#---- library --------------------------------------------------------------------------------------

@dataclass
class Track:
    path: str
    mtime: float
    rejected: bool = False
    order: int = 0

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass
class Library:
    root: str
    nope_path: str
    pool: list[tuple[str, float]] = field(default_factory=list)
    blocked: int = 0
    total: int = 0
    errors: list[str] = field(default_factory=list)
    _parts: dict[str, tuple[str, str]] = field(default_factory=dict)

    def load(self):
        self.errors.clear()
        patterns = self._nope()
        entries = []
        try:
            with os.scandir(self.root) as it:
                for e in it:
                    if e.name.lower().endswith(".mp3") and e.is_file():
                        entries.append((e.path, e.stat().st_mtime, e.name))
        except OSError as exc:
            self.errors.append(str(exc))
        self.total = len(entries)
        allowed = [(p, m) for p, m, n in entries if not any(r.search(n) for r in patterns)]
        self.blocked = self.total - len(allowed)
        allowed.sort(key=lambda pm: pm[1], reverse=True)
        self.pool = allowed

    def _nope(self) -> list[re.Pattern]:
        patterns = []
        try:
            with open(self.nope_path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            return patterns
        for n, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                patterns.append(re.compile(line, re.IGNORECASE))
            except re.error as exc:
                self.errors.append(f"{os.path.basename(self.nope_path)}:{n}: {exc}")
        return patterns

    def parts(self, path: str) -> tuple[str, str]:
        """Artist and title, falling back to the filename stem when tags are unusable."""
        if path not in self._parts:
            try:
                tags = ID3.read(path)
                artist, title = tags["artist"].strip(), tags["title"].strip()
            except Exception:
                artist = title = ""
            if not title:
                title = os.path.splitext(os.path.basename(path))[0]
            self._parts[path] = (artist, title)
        return self._parts[path]

    def label(self, path: str) -> str:
        return " - ".join(p for p in self.parts(path) if p)


def hotplug_mounts() -> list[str]:
    """Every mounted hotplug (removable) filesystem, in lsblk order."""
    try:
        out = subprocess.run(["lsblk", "-Po", "MOUNTPOINT,HOTPLUG"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        m = re.fullmatch(r'MOUNTPOINT="(.*)" HOTPLUG="(\d)"', line.strip())
        if m and m.group(2) == "1" and m.group(1) and not m.group(1).startswith("["):
            found.append(m.group(1))
    return found


def usb_mountpoint() -> str | None:
    mounts = hotplug_mounts()
    return mounts[-1] if mounts else None


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def age(seconds: float) -> str:
    for unit, size in (("y", 31557600), ("mo", 2629800), ("w", 604800), ("d", 86400), ("h", 3600)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return "now"


#---- app ------------------------------------------------------------------------------------------

def fit(text: str, width: int, centre: bool = False) -> str:
    if width <= 0:
        return ""
    if len(text) > width:
        return text[:width - 1] + "…"
    return text.center(width) if centre else text.ljust(width)


def columns(inner: int) -> tuple[int, int]:
    """Widths of the artist and title columns for a row of the given inner width."""
    text_w = inner - STAMP_W - 4
    if text_w < 16:
        return 0, max(0, text_w)
    artist_w = max(8, min(60, text_w * 2 // 5))
    return artist_w, text_w - artist_w - 1


def put(win, y, x, text, attr=0, width=None):
    h, w = win.getmaxyx()
    if not (0 <= y < h) or x >= w:
        return
    avail = w - x if width is None else min(width, w - x)
    if avail > 0:
        try:
            win.addnstr(y, x, text, avail, attr)
        except curses.error:
            pass


class App:
    def __init__(self, stdscr, library):
        self.scr = stdscr
        self.lib = library
        self.tab = TABS[0]
        self.groups: dict[str, list[Track]] = {t: [] for t in TABS}
        self.n = {"recent": N_RECENT, "random": N_RANDOM}
        self._sel = {t: 0 for t in TABS}
        self._top = {t: 0 for t in TABS}
        self.rejected: set[str] = set()
        self.sorted_random = False
        self.prompt = None
        self.progress = None
        self._seq = 0
        self.toast = None
        self.running = True

    # --- per-tab view state ---------------------------------------------
    @property
    def rows(self) -> list[Track]:
        return self.groups[self.tab]

    @property
    def sel(self) -> int:
        return self._sel[self.tab]

    @sel.setter
    def sel(self, value: int):
        self._sel[self.tab] = value

    @property
    def top(self) -> int:
        return self._top[self.tab]

    @top.setter
    def top(self, value: int):
        self._top[self.tab] = value

    def switch(self, delta: int):
        self.tab = TABS[(TABS.index(self.tab) + delta) % len(TABS)]

    # --- selection ------------------------------------------------------
    def _available(self, used):
        return [(p, m) for p, m in self.lib.pool if p not in used and p not in self.rejected]

    def _used(self):
        return {t.path for group in self.groups.values() for t in group}

    def _clamp_sel(self, tab):
        self._sel[tab] = min(self._sel[tab], max(0, len(self.groups[tab]) - 1))

    def _track(self, path, mtime) -> Track:
        self._seq += 1
        return Track(path, mtime, order=self._seq)

    def regenerate(self):
        recent = [self._track(p, m) for p, m in self._available(set())[:self.n["recent"]]]
        self.groups = {"recent": recent, "random": []}
        self.reroll_random()
        for tab in TABS:
            self._clamp_sel(tab)

    def reroll_random(self):
        pool = self._available({t.path for t in self.groups["recent"]})
        picks = random.sample(pool, min(self.n["random"], len(pool)))
        self.groups["random"] = [self._track(p, m) for p, m in picks]
        self.resort()
        self._clamp_sel("random")

    def rebalance(self, tab):
        """Grow or trim one tab in place, leaving its existing picks untouched."""
        have, want = self.groups[tab], self.n[tab]
        if len(have) > want:
            self.groups[tab] = have[:want]
        elif len(have) < want:
            need = want - len(have)
            if tab == "recent":
                extra = self._available({t.path for t in have})[:need]
            else:
                pool = self._available(self._used())
                extra = random.sample(pool, min(need, len(pool)))
            self.groups[tab] = have + [self._track(p, m) for p, m in extra]
            if tab == "recent":
                self.evict_duplicates({p for p, _ in extra})
            else:
                self.resort()
        self._clamp_sel(tab)

    def evict_duplicates(self, paths) -> int:
        """A track promoted into Recent must not also sit in Random; reroll any that clash."""
        clashes = [i for i, t in enumerate(self.groups["random"]) if t.path in paths]
        if not clashes:
            return 0
        pool = self._available(self._used())
        picks = random.sample(pool, min(len(clashes), len(pool)))
        if not picks:
            self.notify("no unused tracks left to reroll", C_WARN)
            return 0
        for i, (path, mtime) in zip(clashes, picks):
            self.groups["random"][i] = self._track(path, mtime)
        self.resort()
        plural = "s" if len(picks) > 1 else ""
        self.notify(f"rerolled {len(picks)} random track{plural} now in Recent", C_WARN)
        return len(picks)

    def _arrange(self, key_fn):
        group = self.groups["random"]
        self._clamp_sel("random")
        keep = group[self._sel["random"]].path if group else None
        group.sort(key=key_fn)
        if keep:
            self._sel["random"] = next((i for i, t in enumerate(group) if t.path == keep),
                                       self._sel["random"])

    def resort(self):
        if self.sorted_random:
            self._arrange(lambda t: [s.lower() for s in self.lib.parts(t.path)])

    def toggle_sort(self):
        self.sorted_random = not self.sorted_random
        if self.sorted_random:
            self.resort()
            self.notify("sorted by artist, then title")
        else:
            self._arrange(lambda t: t.order)
            self.notify("restored sampled order")

    def exclude_current(self):
        """Toggle exclusion on the Recent tab; swap in a fresh track on the Random tab."""
        if not self.rows:
            return
        t = self.rows[self.sel]
        if self.tab == "recent":
            t.rejected = not t.rejected
            if t.rejected:
                self.rejected.add(t.path)
                self.notify(f"excluded {self.lib.label(t.path)}", C_DIM)
            else:
                self.rejected.discard(t.path)
                self.notify(f"restored {self.lib.label(t.path)}", C_ACCENT)
            return
        self.rejected.add(t.path)
        pool = self._available(self._used())
        if not pool:
            self.notify("no unused tracks left", C_WARN)
            return
        path, mtime = random.choice(pool)
        self.rows[self.sel] = self._track(path, mtime)
        self.resort()
        self.notify(f"swapped in {self.lib.label(path)}", C_ACCENT)

    # --- actions --------------------------------------------------------
    def edit_nope(self):
        """Hand the terminal to $EDITOR for the exclusion file, then reload."""
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "sensible-editor"
        curses.def_prog_mode()
        curses.endwin()
        try:
            subprocess.call(shlex.split(editor) + [self.lib.nope_path])
        except OSError as exc:
            curses.reset_prog_mode()
            self.notify(f"{editor}: {exc}", C_WARN)
            return
        curses.reset_prog_mode()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        curses.flushinp()
        self.scr.clear()
        self.reload()

    def playlist(self) -> list[Track]:
        return [t for group in self.groups.values() for t in group if not t.rejected]

    def drive_ok(self, target: str) -> str | None:
        """Refuse anything that is not, right now, a mounted removable drive."""
        real = os.path.realpath(target)
        music = os.path.realpath(self.lib.root)
        if real.count(os.sep) < 2 or real in ("/", os.path.expanduser("~")):
            return f"refusing to touch {real}"
        if music == real or music.startswith(real + os.sep) or real.startswith(music + os.sep):
            return "library and drive overlap"
        if not os.path.ismount(real):
            return f"{real} is not a mountpoint"
        if real not in [os.path.realpath(m) for m in hotplug_mounts()]:
            return "drive is no longer mounted"
        return None

    def ask_write(self):
        target = usb_mountpoint()
        if not target:
            self.notify("no hotplug drive mounted", C_WARN)
            return
        tracks = self.playlist()
        if not tracks:
            self.notify("nothing to write", C_WARN)
            return
        size = sum(os.path.getsize(t.path) for t in tracks if os.path.exists(t.path))
        capacity = shutil.disk_usage(target).total
        if size > capacity * 0.98:
            self.notify(f"{human(size)} will not fit on {human(capacity)} drive", C_WARN)
            return
        folders = -(-len(tracks) // CHUNK)
        self.prompt = ([f"Write {len(tracks)} tracks ({human(size)}) to {target}?",
                        f"They will be split across {folders} folders of up to {CHUNK}."],
                       lambda: self.ask_wipe(target))

    def ask_wipe(self, target: str):
        self.prompt = ([f"This ERASES everything already on {target}.",
                        "Every file and folder on the drive will be deleted.",
                        "Continue?"],
                       lambda: self.write_tracks(target))

    def wipe(self, target: str) -> list[str]:
        """Delete every entry directly under the drive root. Never follows symlinks out."""
        errors = []
        for entry in os.scandir(target):
            try:
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path)
                else:
                    os.unlink(entry.path)
            except OSError as exc:
                errors.append(f"{entry.name}: {exc.strerror}")
        return errors

    def write_tracks(self, target: str):
        problem = self.drive_ok(target)
        if problem:
            self.notify(problem, C_WARN)
            return
        tracks = self.playlist()
        chunks = [tracks[i:i + CHUNK] for i in range(0, len(tracks), CHUNK)]
        total = len(tracks)

        self.progress = (0, total, "erasing drive…", target)
        self.draw()
        errors = self.wipe(target)

        done, aborted, last = 0, False, 0.0
        self.scr.timeout(0)
        for n, chunk in enumerate(chunks):
            folder = os.path.join(target, f"{n + 1:02d}x{len(chunk)}")
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as exc:
                errors.append(f"{os.path.basename(folder)}: {exc.strerror}")
                break
            used = set()
            for t in chunk:
                name = os.path.basename(t.path).translate(FAT_INVALID)
                stem, ext = os.path.splitext(name)
                dup = 2
                while name.lower() in used:          # sanitising can collide
                    name, dup = f"{stem} ({dup}){ext}", dup + 1
                used.add(name.lower())
                try:
                    shutil.copy2(t.path, os.path.join(folder, name))
                except (OSError, shutil.Error) as exc:
                    errors.append(f"{name}: {exc}")
                done += 1
                now = time.monotonic()
                if now - last > 0.08 or done == total:
                    self.progress = (done, total, f"{os.path.basename(folder)}/{name}", target)
                    self.draw()
                    last = now
                if self.scr.getch() in (27, ord("q")):
                    aborted = True
                    break
            if aborted:
                break

        self.progress = (done, total, "flushing to disk…", target)
        self.draw()
        os.sync()
        self.progress = None
        if aborted:
            self.notify(f"aborted after {done}/{total} files — drive left part-written", C_WARN)
        elif errors:
            self.notify(f"{done - len(errors)}/{total} written, {len(errors)} failed: {errors[0]}",
                        C_WARN)
        else:
            self.notify(f"wrote {total} tracks to {len(chunks)} folders on {target}")

    # --- state ----------------------------------------------------------
    def notify(self, text, level=C_ACCENT):
        self.toast = (text, level, time.monotonic() + TOAST_SECONDS)

    def live_toast(self):
        if self.toast and time.monotonic() >= self.toast[2]:
            self.toast = None
        return self.toast

    def move(self, delta, page=1):
        if self.rows:
            self.sel = max(0, min(len(self.rows) - 1, self.sel + delta * page))

    def clamp_scroll(self, rows):
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + rows:
            self.top = self.sel - rows + 1
        self.top = max(0, min(self.top, max(0, len(self.rows) - rows)))

    # --- drawing --------------------------------------------------------
    def draw_topbar(self, w):
        attr = curses.color_pair(C_BAR) | curses.A_BOLD
        put(self.scr, 0, 0, " " * w, attr)
        put(self.scr, 0, 1, f"{APP_NAME} {VERSION}  ·  {self.lib.root}", attr)
        right = f"{self.sel + 1}/{len(self.rows)}  ·  {self.lib.total} in library "
        put(self.scr, 0, max(0, w - len(right)), right, attr)

    def draw_tabs(self, y, w):
        x = 2
        for tab in TABS:
            label = f" {tab.capitalize()} {len(self.groups[tab])} "
            attr = (curses.color_pair(C_TAB) | curses.A_BOLD if tab == self.tab
                    else curses.color_pair(C_TAB_OFF))
            put(self.scr, y, x, label, attr, max(0, w - 2 - x))
            x += len(label) + 1

    def draw_panel(self, top, bottom, w):
        rows = bottom - top - 1
        self.clamp_scroll(rows)
        border = curses.color_pair(C_BORDER)
        put(self.scr, top, 0, "╭" + "─" * (w - 2) + "╮", border)
        put(self.scr, bottom, 0, "╰" + "─" * (w - 2) + "╯", border)
        self.draw_tabs(top, w)

        inner = w - 2
        widths = columns(inner)
        now = time.time()
        for row in range(rows):
            y = top + 1 + row
            put(self.scr, y, 0, "│", border)
            put(self.scr, y, w - 1, "│", border)
            i = self.top + row
            if i < len(self.rows):
                self.draw_row(y, i, inner, widths, now)
        self.draw_scrollbar(top + 1, rows, w)

    def draw_row(self, y, i, inner, widths, now):
        t = self.rows[i]
        stamp = age(now - t.mtime)
        artist, title = self.lib.parts(t.path)
        artist_w, title_w = widths
        cells = [("×" if t.rejected else " ", curses.color_pair(C_DEAD)),
                 (" ", 0),
                 (fit(artist, artist_w), curses.color_pair(C_ARTIST)),
                 (" ", 0),
                 (fit(title, title_w), 0),
                 (" ", 0),
                 (stamp.rjust(STAMP_W), curses.color_pair(C_DIM)),
                 (" ", 0)]
        if i == self.sel:
            cells = [(text, curses.color_pair(C_SEL) | curses.A_BOLD) for text, _ in cells]
        elif t.rejected:
            cells = [(text, curses.color_pair(C_DEAD) | curses.A_DIM) for text, _ in cells]
        x = 1
        for text, attr in cells:
            put(self.scr, y, x, text, attr, inner + 1 - x)
            x += len(text)

    def draw_scrollbar(self, y0, rows, w):
        total = len(self.rows)
        if total <= rows:
            return
        size = max(1, rows * rows // total)
        start = (self.top * (rows - size)) // max(1, total - rows)
        for row in range(rows):
            glyph = "█" if start <= row < start + size else "│"
            put(self.scr, y0 + row, w - 1, glyph, curses.color_pair(C_BORDER))

    def draw_footer(self, y, w):
        """Toast (else the selected filename) on the left, hints on the right unless they clash."""
        if toast := self.live_toast():
            left, attr = f"● {toast[0]}", curses.color_pair(toast[1])
        else:
            left = self.rows[self.sel].name if self.rows else "no tracks"
            attr = curses.color_pair(C_DIM)
        hints = HINTS[self.tab]
        x = max(1, w - len(hints) - 1)
        if toast and len(left) > x - 3:      # a toast keeps the footer to itself
            x = w
        else:
            put(self.scr, y, x, hints, curses.color_pair(C_DIM), w - 1 - x)
        if x - 3 > 8 or toast:
            put(self.scr, y, 1, fit(left, x - 3), attr)

    def draw_box(self, h, w, lines, width=None):
        """Centred rounded box; lines are (text, attr) pairs. Fixed width if given."""
        bw = width or min(w - 8, max(len(text) for text, _ in lines) + 6)
        bh = len(lines) + 2
        y0, x0 = max(1, (h - bh) // 2), max(0, (w - bw) // 2)
        border = curses.color_pair(C_WARN)
        put(self.scr, y0, x0, "╭" + "─" * (bw - 2) + "╮", border)
        put(self.scr, y0 + bh - 1, x0, "╰" + "─" * (bw - 2) + "╯", border)
        for i, (text, attr) in enumerate(lines):
            y = y0 + 1 + i
            put(self.scr, y, x0, "│", border)
            put(self.scr, y, x0 + bw - 1, "│", border)
            put(self.scr, y, x0 + 1, fit(text, bw - 2, centre=True), attr, bw - 2)

    def draw_prompt(self, h, w):
        lines, _ = self.prompt
        body = [(line, 0) for line in lines]
        body += [("", 0), ("[y] yes    [n] no", curses.color_pair(C_TAB) | curses.A_BOLD)]
        self.draw_box(h, w, body)

    def draw_progress(self, h, w):
        done, total, note, target = self.progress
        bw = max(40, min(w - 8, w * 7 // 10))
        count = f"{done:>{len(str(total))}}/{total}"
        bar_w = max(10, bw - len(count) - 8)
        filled = bar_w * done // max(1, total)
        bar = "█" * filled + "░" * (bar_w - filled)
        self.draw_box(h, w, [(f"Writing to {target}", 0),
                             (f"{bar}  {count}", curses.color_pair(C_ACCENT)),
                             (note, curses.color_pair(C_DIM)),
                             ("", 0),
                             ("esc to abort", curses.color_pair(C_DIM))], width=bw)

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        if h < 6 or w < 30:
            put(self.scr, 0, 0, "terminal too small")
        else:
            self.draw_topbar(w)
            self.draw_panel(1, h - 2, w)
            self.draw_footer(h - 1, w)
            if self.progress:
                self.draw_progress(h, w)
            elif self.prompt:
                self.draw_prompt(h, w)
        self.scr.noutrefresh()
        curses.doupdate()

    # --- input ----------------------------------------------------------
    def handle(self, key):
        h, _ = self.scr.getmaxyx()
        page = max(1, h - 4)
        if self.prompt:
            _, action = self.prompt
            if key in (ord("y"), ord("Y")):
                self.prompt = None
                action()
            elif key in (ord("n"), ord("N"), 27, 10, 13):
                self.prompt = None
                self.notify("cancelled", C_DIM)
            return
        if key in (ord("q"), 27):
            self.running = False
        elif key in (curses.KEY_DOWN, ord("j")):
            self.move(1)
        elif key in (curses.KEY_UP, ord("k")):
            self.move(-1)
        elif key == curses.KEY_NPAGE:
            self.move(1, page)
        elif key == curses.KEY_PPAGE:
            self.move(-1, page)
        elif key in (curses.KEY_HOME, ord("g")):
            self.sel = 0
        elif key in (curses.KEY_END, ord("G")):
            self.sel = max(0, len(self.rows) - 1)
        elif key in (9, curses.KEY_RIGHT, ord("l")):
            self.switch(1)
        elif key in (curses.KEY_BTAB, curses.KEY_LEFT, ord("h")):
            self.switch(-1)
        elif key in (curses.KEY_DC, ord("x")):
            self.exclude_current()
        elif key == ord("R") and self.tab == "random":
            self.reroll_random()
            self.notify(f"rerolled {len(self.groups['random'])} random tracks")
        elif key == ord("s") and self.tab == "random":
            self.toggle_sort()
        elif key == ord("B"):
            self.edit_nope()
        elif key == ord("y"):
            self.ask_write()
        elif key == ord("L"):
            self.reload()
        elif key in (ord("["), ord("]")):
            self.n[self.tab] = max(0, self.n[self.tab] + (STEP if key == ord("]") else -STEP))
            self.rebalance(self.tab)

    def reload(self):
        self.lib.load()
        self.regenerate()
        if self.lib.errors:
            self.notify(self.lib.errors[0], C_WARN)
        else:
            self.notify(f"{len(self.lib.pool)} eligible · {self.lib.blocked} filtered out")

    def run(self):
        self.reload()
        while self.running:
            self.draw()
            self.scr.timeout(250 if self.live_toast() else -1)
            key = self.scr.getch()
            if key in (-1, curses.KEY_RESIZE):
                continue
            self.handle(key)


#---- ID3 tag parser --------------------------------------------------------------------------------

class ID3:
    """Minimal dependency-free reader for the artist/title/album tags."""

    @staticmethod
    def read(path: str) -> dict[str, str]:
        v2, v1 = ID3._v2(path), ID3._v1(path)
        return {k: v2.get(k) or v1.get(k, "") for k in ("artist", "title", "album")}

    @staticmethod
    def _decode(data: bytes) -> str:
        if not data: return ""
        codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(data[0], "latin-1")
        try: text = data[1:].decode(codec)
        except: text = data[1:].decode("latin-1", "replace")
        return text.split("\x00")[0]

    @staticmethod
    def _v2(path: str) -> dict[str, str]:
        def synch(b):
            return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]

        with open(path, "rb") as f:
            if (head := f.read(10))[:3] != b"ID3": return {}
            version, flags = head[3], head[5]
            body = f.read(synch(head[6:10]))

        if flags & 0x80: return {} # unsynchronised: skip rather than misparse
        if flags & 0x40: # skip extended header
            n = synch(body[:4]) if version == 4 else 4 + struct.unpack(">I", body[:4])[0]
            body = body[n:]

        small = version == 2 # v2.2 uses 3-byte IDs & sizes, no frame flags
        fields = ({"TP1": "artist", "TT2": "title", "TAL": "album"} if small
                  else {"TPE1": "artist", "TIT2": "title", "TALB": "album"})
        tags, pos, hdr = {}, 0, 6 if small else 10

        while pos + hdr <= len(body):
            if small: fid, size = body[pos:pos+3], int.from_bytes(body[pos+3:pos+6], "big")
            else:
                fid = body[pos:pos+4]
                size = (synch(body[pos+4:pos+8]) if version == 4
                        else struct.unpack(">I", body[pos+4:pos+8])[0])

            if not fid.strip(b"\x00") or size <= 0: break

            key = fields.get(fid.decode("latin-1", "replace"))
            if key and key not in tags: tags[key] = ID3._decode(body[pos+hdr:pos+hdr+size])
            pos += hdr + size

        return tags

    @staticmethod
    def _v1(path: str) -> dict[str, str]:
        try:
            with open(path, "rb") as f:
                f.seek(-128, os.SEEK_END)
                tag = f.read(128)
        except OSError: return {}
        if tag[:3] != b"TAG": return {}
        fld = lambda b: b.decode("latin-1", "replace").rstrip("\x00").rstrip()
        return {"title": fld(tag[3:33]), "artist": fld(tag[33:63]), "album": fld(tag[63:93])}


#---- entry ----------------------------------------------------------------------------------------

def main(stdscr):
    init_colors()
    curses.curs_set(0)
    lib = Library(os.path.expanduser(MUSIC_DIR), os.path.expanduser(NOPE_FILE))
    App(stdscr, lib).run()


if __name__ == "__main__":
    locale.setlocale(locale.LC_ALL, "")
    curses.wrapper(main)
