# CLI Tools

Some command line tools, mostly in Python 3, which automate tasks I frequently find myself performing, including downloading audio from YouTube, editing and merging PDFs, and more.

## Overview

As the need arises, I will certainly be expanding my collection of scripts, and new additions will be added. Some functionality of certain scripts is really nothing but a wrapper for an underlying command-line utility, such as qpdf or sox; in such cases, the script is designed to simplify the syntax and use of these tools for certain highly specific applications, as needed.

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

The scripts in general may require positional command-line arguments, as well as optional arguments and flags. Argument parsing is done in Python using the ```argparse``` module, and all arguments are thoroughly documented. To view help, simply execute a script with the ```-h``` or ```--help``` flag.

## Details of Scripts


