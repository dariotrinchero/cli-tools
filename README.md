# CLI Tools

Some command line tools, mostly in Python 3, which automate tasks I frequently find myself performing. This includes downloading audio from YouTube, editing and merging PDFs, and more. As the need arises, I will certainly expand this collection.

## Overview

Scripts range from highly specific in utility (as with, say, ```distribute.py```) to fairly broad (as with, say, ```pdftools.py```). Certain script functionality is really little but a wrapper for an underlying pre-existing command-line utility, such as ```qpdf``` or ```sox```; in such cases, the script is designed to simplify the syntax and make the use of these tools more convenient and intuitive for whatever highly-specific application I have use of.

### Overview of Scripts

* ```distribute.py``` - made for *Toyota Yarris* sound-system; arranges mp3 files alphabetically into directories of 255 each, and compares the files with those in ```~/Music```
* ```download-music.py``` - downloads mp3 audio from a list of YouTube (or other) URLs, removing leading and trailing silence
* ```make-shortcut.sh``` - creates a .desktop shortcut for a URL, for use with Ubuntu
* ```pdftools.py``` - extract, interleave or rotate pages of PDFs, or find and replace text in a PDF
* ```pwned.py``` - query [HaveIBeenPwned](https://haveibeenpwned.com/API/v2#SearchingPwnedPasswordsByRange) API to check whether given password has been leaked in a data breach
* ```url-desktop.py``` - convert internet shortcuts between Windows (.url) and Ubuntu (.desktop) format

### Running the Scripts

Each script begins with an appropriate shebang, allowing it to be executed directly with, for instance
```
./scriptname.py
```
in place of
```
python3 scriptname.py
```

For maximum convenience, it is recommended to place the desired scripts in a folder linked by the ```$PATH``` environment variable. That is, if the scripts are in ```~/scripts```, then the following line may be added to the shell's RC file, to allow scripts to be executed by name from a terminal open in any directory:
```
export PATH=$PATH:~/scripts
```

### Accessing Documentation

Each script in general takes positional command-line arguments, as well as optional arguments and flags. Argument parsing is done in Python using the ```argparse``` module, and all arguments are thoroughly documented. To view help, simply execute a script with the ```-h``` or ```--help``` flag.

## Details of Scripts
