#!/bin/bash

while true; do
	# Python needs to be in $PATH
	python3 serverlist_bot.py
	if [ $? -ne 0 ]; then
		break
	fi
done

