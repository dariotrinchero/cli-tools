#!/bin/zsh

# A simple script to be bound to a keyboard shortcut (via, say, gsd-media-keys) to toggle dark-mode.
#
# Specifically, script switches between gtk themes 'Yaru' and 'Yaru-dark', toggles orange-filter,
# & attempts to change shell theme to 'Yaru-dark' when applicable.
#
# Requires package: gnome-shell-extensions
# Gnome extensions must be enabled (can be done via package: gnome-tweaks).
#
# Some apps (eg. Clementine) use Qt5 instead of gtk, so may not respect dark mode.
# This can be fixed via packages: qt5ct qt5-style-plugins

curr_theme=$(gsettings get org.gnome.desktop.interface gtk-theme)

if [ $curr_theme = "'Yaru'" ]
then
	gsettings set org.gnome.desktop.interface gtk-theme 'Yaru-dark'
	gsettings set org.gnome.shell.extensions.user-theme name 'Yaru-dark'
	gsettings set org.gnome.settings-daemon.plugins.color night-light-enabled true
else
	gsettings set org.gnome.desktop.interface gtk-theme 'Yaru'
	gsettings set org.gnome.shell.extensions.user-theme name ''
	gsettings set org.gnome.settings-daemon.plugins.color night-light-enabled false
fi
