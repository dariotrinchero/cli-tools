#!/bin/bash

set -euo pipefail # for safety: exit immediately if any command fails

#-----------------------------------------------------------------------------------------------------------
# User configuration
#-----------------------------------------------------------------------------------------------------------

src=~/Music

num_recent=40
num_random=280

# organize files into directories of at most this many files
batch_size=255

#-----------------------------------------------------------------------------------------------------------
# Terminal output formatting
#-----------------------------------------------------------------------------------------------------------

# ANSI escape codes
RED='\033[0;31m'
YELLOW='\033[1;33m'
DIM='\033[2m'
NC='\033[0m' # reset

# script prefix
PRE='[yipyip] '

#-----------------------------------------------------------------------------------------------------------
# Main script
#-----------------------------------------------------------------------------------------------------------

# detect most recently connected mounted removable device
dst=$(lsblk -Po MOUNTPOINT,HOTPLUG | awk -F '"' '$4==1 && $2!="" {print $2}' | tail -n1)
if [[ -z "$dst" ]]; then
    echo -e "${PRE}${RED}Error: No removable USB drive detected.${NC}"
    exit 1
fi

# confirm destination
echo -e "${PRE}${YELLOW}Syncing to:${NC} $dst"
echo -e "${PRE}${YELLOW}All existing files on this drive will be erased.${NC}"
read -rp "${PRE}Proceed? [y/N] " reply
case "$reply" in
    [yY][eE][sS]|[yY]) ;;
    *) echo -e "${PRE}${RED}Aborted.${NC}"; exit 1 ;;
esac

# select tracks
echo -e "${PRE}Collecting $num_recent recent tracks & $num_random random tracks."
mapfile -t recent < <(
    find "$src" -type f -iname '*.mp3' -printf '%T@ %p\n' \
    | sort -nr \
    | head -n "$num_recent" \
    | cut -d' ' -f2-
)
mapfile -t random < <(
    find "$src" -type f -iname '*.mp3' \
    | grep -vxFf <(printf '%s\n' "${recent[@]}") \
    | shuf -n "$num_random"
)
mapfile -t files < <(
    printf '%s\n' "${recent[@]}" "${random[@]}" \
	| sort --ignore-case
)

# wipe drive
echo -e "${PRE}Wiping drive...${DIM}"
rm -rfv "$dst"/*

# variables for output
total_files=$((num_recent + num_random))
batches=$(((total_files + batch_size - 1) / batch_size))
batch=1

# copy new files
echo -e "${NC}${PRE}Beginning file transfer."
echo -e "${PRE}Transferring $total_files files in $batches batches of ≤$batch_size files.${DIM}"
for ((i=0; i<${#files[@]}; i+=batch_size)); do
	echo -e "${NC}${PRE}Beginning batch $((batch++)).${DIM}"
    subdir="$dst/$((i/batch_size+1))x255"
    rsync -a --progress \
      --files-from=<(printf '%s\n' "${files[@]:i:batch_size}" | sed "s|^$src/||") \
      "$src"/ "$subdir"/
done
echo -e "${NC}${PRE}Done."
