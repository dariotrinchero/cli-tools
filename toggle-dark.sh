#!/bin/zsh

# A simple script to be bound to a keyboard shortcut (via, say, gsd-media-keys) to toggle dark-mode.

curr_theme=$(gsettings get org.gnome.desktop.interface gtk-theme)

if [ $curr_theme = "'Yaru'" ]
then
	gsettings set org.gnome.desktop.interface gtk-theme 'Yaru-dark'
	gsettings set org.gnome.settings-daemon.plugins.color night-light-enabled true
else
	gsettings set org.gnome.desktop.interface gtk-theme 'Yaru'
	gsettings set org.gnome.settings-daemon.plugins.color night-light-enabled false
fi
