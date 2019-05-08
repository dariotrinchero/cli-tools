#!/bin/bash

# Get name and URL

printf "Create New Internet Shortcut\n\n[URL]: \033[38;5;157m\033[2m"
read url
printf "\033[0m[Name]: \033[38;5;157m"
read name
printf "\033[0m\n"

# Make shortcut

echo "[Desktop Entry]" >> "$name.desktop"
echo "Encoding=UTF-8" >> "$name.desktop"
echo "Name=$name" >> "$name.desktop"
echo "Type=Link" >> "$name.desktop"
echo "URL=$url" >> "$name.desktop"
echo "Icon=firefox" >> "$name.desktop"
echo "Name[en-ZA]=$name" >> "$name.desktop"

# Report whether successful

if [ $? -eq 0 ]; then
    printf "\033[92mShortcut created\033[0m\n"
else
    printf "\033[91mCould not create shortcut\033[0m\n"
fi
