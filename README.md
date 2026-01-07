# Command Line Tools

Some basic command line tools, mostly in Python 3, which automate tasks I frequently find myself
performing.

## Overview

Scripts range from highly specific in utility (such as `distribute.py`) to fairly broad (such as
`pdftools.py`). Certain script functionality is little but a wrapper for a pre-existing command line
utility like `qpdf`; in such cases, the script is designed to simplify the syntax and make the use
of these tools more convenient and intuitive for whatever highly-specific application I require.

### Description of Scripts

* `appa-yip-yip.py` - copies combination of random & recent music tracks from `~/Music` onto USB
  drive (automatically detected), replacing all files on the drive; this is to keep a fresh rotating
  sample of a large music library available for playback on the go (having been made for the
  *Toyota Yarris* sound-system, the script groups files into directories of 255 each, as the
  sound-system cannot read larger directories)
* `ergo.py` - made to assist with card game, *Ergo*; outputs a list of atomic proposition which are
  (dis)proven by a given list of premises (propositional logic sentences)
* `shortcut.py` - creates cross-platform, browser independent internet shortcut for a URL, based on
  a .html file, with options to retrieve URL from clipboard, and to use (sanitised) webpage title
  for shortcut name
* `pdftools.py` - extract, interleave or rotate pages of PDFs, or find and replace text in a PDF
* `pwned.py` - query
  [HaveIBeenPwned](https://haveibeenpwned.com/API/v2#SearchingPwnedPasswordsByRange) API to check
  whether a given password has been leaked in a data breach
* `repos.sh` - output summary of status of a number of git repos, all assumed to reside in
  `~/git-repos`
* `news.py` - output recent news headlines scraped from Wikipedia 'current events' portal

### Running the Scripts

Each script begins with an appropriate shebang, allowing it to be executed directly, for instance
with `./scriptname.py` in place of `python3 scriptname.py`.

For maximum convenience, it is recommended to place the desired scripts in a folder linked by the
`$PATH` environment variable. That is, if the scripts are in `~/scripts`, then the following line
may be added to the shell's RC file, to allow scripts to be executed by name from a terminal open 
in any directory:
```bash
export PATH=$PATH:~/scripts
```

## Documentation

Each script in general takes positional command line arguments, as well as optional arguments and
flags. Argument parsing is done in Python using the `argparse` module, and all arguments are
thoroughly documented. To view detailed help, simply execute a script with the `-h` or `--help`
flag.

## Prerequisites

* `pdftools.py` requires command line tool [QPDF](http://qpdf.sourceforge.net/)

## Known Issues

* `distribute.py` is not very robust. Manually rearranging the files in each of the
  generated folders can break the alphabetical ordering.

## Contributing

Pull requests are welcome - in fact, encouraged.
