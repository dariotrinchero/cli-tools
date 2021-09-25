# Command Line Tools

Some basic command line tools, mostly in Python 3, which automate tasks I frequently find myself
performing. This includes downloading audio from YouTube, editing and merging PDFs, and more. As
the need arises, I will certainly expand this collection.

## Overview

Scripts range from highly specific in utility (such as `distribute.py` or `uni-shell.py`) to fairly
broad (such as `pdftools.py`). Certain script functionality is little but a wrapper for a
pre-existing command line utility like `qpdf` or `sox`; in such cases, the script is designed to
simplify the syntax and make the use of these tools more convenient and intuitive for whatever
highly-specific application I require.

### Description of Scripts

* `distribute.py` - made for *Toyota Yarris* sound-system; arranges mp3 files alphabetically into
  directories of 255 each, and compares the files with those in `~/Music`
* `download-music.py` - downloads mp3 audio from a list of YouTube (or other) URLs, removing 
  leading and trailing silence
* `shortcut.py` - creates cross-platform, browser independent internet shortcut for a URL,
  based on a .html file, with options to retrieve URL from clipboard, and to use (sanitised) webpage
  title for shortcut name
* `pdftools.py` - extract, interleave or rotate pages of PDFs, or find and replace text in a PDF
* `pwned.py` - query
  [HaveIBeenPwned](https://haveibeenpwned.com/API/v2#SearchingPwnedPasswordsByRange) API to check
  whether a given password has been leaked in a data breach
* `uni-shell.py` - made for *Stellenbosch University* Ubuntu servers; launch FortiClient VPN tunnel
  then connect to given host with SSH or SFTP, and transfer control to user

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

* `download-music.py` requires command line tools [SoX](http://sox.sourceforge.net/) and 
  [youtube-dl](https://ytdl-org.github.io/youtube-dl/index.html)
* `pdftools.py` requires command line tool [QPDF](http://qpdf.sourceforge.net/)
* `uni-shell.py` requires [FortiClient VPN](https://forticlient.com/downloads)

## Potential Issues

* `uni-shell.py` was written for a specific application. It functions by spawning processes, then
  expecting specific output and responding. It is likely that different versions of *FortiClient*
  or different hosts will cause the expected output to differ. If so, the script will require 
  basic modification to function.

## Contributing

Pull requests are welcome - in fact, encouraged.
