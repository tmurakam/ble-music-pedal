#!/bin/bash
# Connect to the CircuitPython serial REPL over USB.
# Exit with <command-prefix>, K, Y.

port=$(ls /dev/tty.usbmodem* 2>/dev/null | head -n 1)

if [ -z "$port" ]; then
    echo "No /dev/tty.usbmodem* device found. Is the board connected?" >&2
    exit 1
fi

echo "Connecting to $port ..."
exec screen "$port" 115200
