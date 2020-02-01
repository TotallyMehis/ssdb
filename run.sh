#!/bin/bash

while true; do
	# Python needs to be in $PATH
	python3 ssdb.py
	if [ $? -ne 0 ]; then
		break
	fi
done
