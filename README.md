# CLI Tools

Some command line tools, mostly in Python 3, which automate tasks I frequently find myself performing, including downloading audio from YouTube, editing and merging PDFs, and more.

## Overview

As the need arises, I will certainly be expanding my collection of scripts, and new additions will be added. Some functionality of certain scripts is really nothing but a wrapper for an underlying command-line utility, such as qpdf or sox; in such cases, the script is designed to simplify the syntax and use of these tools for certain highly specific applications, as needed.

### Running the Scripts

Each Python script begins with a *shebang*, allowing it to be executed directly from the command line by typing
```
./scriptname.py
```
in place of
```
python3 scriptname.py
```

For maximum convenience, it is recommended to place the desired scripts in a folder linked by the ```$PATH``` environment variable. That is, if the scripts are in ```~/scripts```, then the following line is added to the RC file of the shell:
```
export PATH=$PATH:~/scripts
```
This way, the scripts may be executed by name from a terminal open in any directory.

### Accessing Documentation

Stuff

## Details of Scripts


