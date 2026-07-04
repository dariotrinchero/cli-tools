#!/usr/bin/env python3

import argparse
import os
import struct
import sys

DESC_STR = """\

description:
  Create playlist(s) containing every .mp3 in given directory whose filename
  deviates from given format (after Clementine's filename transformations), for
  easy batch application of Clementine's built-in "Organize files..." operation
  (preferred method of fixing misnamed files as it preserves Clementine's
  metadata), as follows:

    1. Preferences > Music Library > DISABLE "Monitor the library for changes".
    2. Import playlist (Playlist > Load playlist...).
    3. Select tracks; Right click > Organize files...; set options:
       - After copying...: Delete the original files
       - Naming options: eg. "%artist - %title - %album.%extension" (match FMT)
       - Overwrite existing files: DISABLED
    4. Check file names look valid, then run.
    5. Repeat steps 2-4 for each playlist generated.
    6. Tools > Do a full library rescan.
    7. Once scan is complete, relaunch "All tracks" smart playlist, & confirm
       that number of tracks matches number of .mp3 files in ~/Music.
"""

DBG_STR = """\

debugging:
  If duplicate tracks appear in Clementine (from neglecting step 1), they can
  be fixed manually by editing the SQLite database:
  
  $ pkill clementine
  $ cp ~/.config/Clementine/clementine.db clementine.db.bak # create backup
  $ sqlite3 ~/.config/Clementine/clementine.db
  $ sqlite> .headers on
  $ sqlite> .mode line
  $ sqlite> SELECT rowid, filename, directory, unavailable, mtime FROM songs
            WHERE title = 'Radio'; -- examine duplicate (change example title)
  $ sqlite> DELETE FROM songs WHERE rowid NOT IN (SELECT MIN(rowid) FROM songs
            GROUP BY filename); -- proceed with deletion
  $ sqlite> .quit
"""

#---- Primary logic --------------------------------------------------------------------------------

# Clementine's "kInvalidFatCharacters"
_SANITIZE = str.maketrans({c: "_" for c in '"*/\\:<>?|'})


def filename_from_tags(tags: dict[str, str], fmt: str) -> str:
    r"""
    Given ID3 tags, return filename in given format, applying Clementine's filename transformations
    (defined in core/organiseformat.cpp of Clementine src):

      1. each of * " / \ : < > ? | becomes '_';
      2. tag value of 0 or -1 becomes empty;
      3. leading '.' in the name becomes '_'.
    """
    sanitize = lambda v: ("" if v in ("0", "-1") else v).translate(_SANITIZE)
    sanitized_tags = {t: sanitize(v) for t, v in tags.items()}
    name = fmt.format(**sanitized_tags)
    if name.startswith("."): name = "_" + name[1:]
    return name + ".mp3"


def write_playlists(paths: list[str], stem: str, batch: int):
    """
    Produce playlist(s) of misnamed tracks for easier batch application of Clementine's "Organize
    files..." operation to these files.
    """
    if batch == 0: batch = len(paths)
    chunks = [paths[i:i+batch] for i in range(0, len(paths), batch)]
    for i, chunk in enumerate(chunks, 1):
        name = f"{stem}_{i:03d}.m3u8" if len(chunks) > 1 else f"{stem}.m3u8"
        with open(name, "w", encoding="utf-8") as f: f.write("\n".join(chunk) + "\n")
        yield name, len(chunk)


def collect_misnamed(music_dir: str, fmt: str, plst_stem: str, batch_size: int) -> None:
    """
    Create playlist(s) with all misnamed MP3 songs in given directory; report progress.
    """
    music_dir = os.path.expanduser(music_dir)
    if not os.path.isdir(music_dir):
        sys.exit("Directory not found: " + music_dir)

    mismatched = []
    for file in sorted(os.listdir(music_dir)):
        path = os.path.join(music_dir, file)
        if not (file.lower().endswith(".mp3") and os.path.isfile(path)): continue
        
        try:
            if file != (expected := filename_from_tags(ID3.read(path), fmt)):
                mismatched.append(path)
                print(f"{path}\n  expected: {expected}")

        except Exception as e: print(f"{path}\n  ERROR: {e}")

    print(f"\n{len(mismatched)} mismatch(es)", file=sys.stderr)
    for name, count in write_playlists(mismatched, plst_stem, batch_size):
        print(f"wrote {name} ({count} tracks)", file=sys.stderr)


#---- ID3 tag parser -------------------------------------------------------------------------------

class ID3:
    """ Minimal dependency-free reader for the artist/title/album tags. """

    @staticmethod
    def read(path: str) -> dict[str, str]:
        """ Read ID3 tags of given file & return artist, title, and album in dict. """
        v2, v1 = ID3._v2(path), ID3._v1(path)
        return {k: v2.get(k) or v1.get(k, "") for k in ("artist", "title", "album")}

    @staticmethod
    def _decode(data: bytes) -> str:
        """ Decode ID3v2 text-frame payload (leading byte selects codec). """
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


#---- Main entry -----------------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=DESC_STR, epilog=DBG_STR,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('--fmt', default='{artist} - {title} - {album}',
        help='filename format (default: "{artist} - {title} - {album}")')
    parser.add_argument('--dir', default='~/Music', help='directory to scan (default: "~/Music")')
    parser.add_argument('--playlist', dest='lst', default='reorg',
        help='name (stem) of playlist(s) (default: "reorg")')
    parser.add_argument('--batch', dest='size', type=int, default=500,
        help='tracks per playlist (default: 500; use 0 for single playlist)')

    args = parser.parse_args()

    collect_misnamed(args.dir, args.fmt, args.lst, args.size)
    print("please view documentation before renaming in Clementine (run with -h)", file=sys.stderr)
