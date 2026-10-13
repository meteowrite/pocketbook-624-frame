#!/bin/sh
# usage: send.sh <workdir> "command" [wait_seconds]
# Sends a command line to the board and prints the console output it produced.
W=$1
before=$(stat -c %s "$W/con.log")
printf '%s\r' "$2" > "$W/con.fifo"
sleep "${3:-2}"
tail -c +$((before + 1)) "$W/con.log" | tr -d '\r'
