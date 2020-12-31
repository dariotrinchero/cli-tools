#!/bin/zsh
#
# A simple script to be bound to a keyboard shortcut (via, say, gsd-media-keys) to toggle audio-loopback.
# 
# Specifically, the script toggles the streaming of audio from microphone input to
# headphone / speaker output.

pactl unload-module module-loopback \
2>&1 | grep "Failed" &> /dev/null && \
	pactl load-module module-loopback latency_msec=1 \
	&> /dev/null && notify-send "Loopback Enabled" "Streaming audio from mic to output" \
|| notify-send "Loopback Disabled" "No longer streaming from mic to output"
