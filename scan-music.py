#!/usr/bin/env python3

import os
import struct
import sys

#---------------------------------------------------------------------------------------------------
# Description
#---------------------------------------------------------------------------------------------------
#
# Create playlist(s) containing every .mp3 in ~/Music whose filename deviates from the convention
#
#    "[artist] - [title] - [album].mp3",
#
# after applying Clementine's filename transformations (see filename_from_tags()), for easy batch
# application of Clementine's built-in "Organize files..." operation (this is the preferred method
# of fixing misnamed files as it preserves metadata in Clementine's internal database).
#

# --- Configurable parameters ----------------------------------------------------------------------

MUSIC_DIR     = "~/Music"    # directory to scan
BATCH_SIZE    = 500          # files per playlist (0 for single playlist)
PLAYLIST_NAME = "reorganize" # name (stem) of playlist(s) in which to collate misnamed files


# --- Main logic -----------------------------------------------------------------------------------

# Clementine's "kInvalidFatCharacters"
_SANITIZE = str.maketrans({c: "_" for c in '"*/\\:<>?|'})


def filename_from_tags(tags: dict[str, str]) -> str:
    r"""
    Given ID3 tags, return filename in format "[artist] - [title] - [album].mp3", applying
    Clementine's filename transformations (defined in core/organiseformat.cpp of Clementine src):

      1. each of * " / \ : < > ? | becomes '_';
      2. tag value of 0 or -1 becomes empty;
      3. leading '.' in the name becomes '_'.
    """
    def sanitize(tag: str) -> str:
        v = tags.get(tag) or ""
        return ("" if v in ("0", "-1") else v).translate(_SANITIZE)

    name = " - ".join(sanitize(k) for k in ("artist", "title", "album"))
    if name.startswith("."): name = "_" + name[1:]
    return name + ".mp3"


def write_playlists(paths: list[str], stem: str = PLAYLIST_NAME, batch: int = BATCH_SIZE):
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


def collect_misnamed(music_dir: str = MUSIC_DIR) -> None:
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
            if file != (expected := filename_from_tags(ID3.read(path))):
                mismatched.append(path)
                print(f"{path}\n  expected: {expected}")

        except Exception as e: print(f"{path}\n  ERROR: {e}")

    print(f"\n{len(mismatched)} mismatch(es).", file=sys.stderr)
    for name, count in write_playlists(mismatched):
        print(f"wrote {name} ({count} tracks)", file=sys.stderr)


# --- ID3 tag parser -------------------------------------------------------------------------------

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


# --- Main entry -----------------------------------------------------------------------------------

if __name__ == "__main__": collect_misnamed()
