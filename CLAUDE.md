# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BLE foot pedal keyboard device for sheet music page-turning. A foot pedal triggers a BLE HID right-arrow keypress sent to an iPad, allowing hands-free page navigation during performance.

## Hardware & Software Stack

- **MCU**: Raspberry Pi Pico W / Pico 2W (CYW43439 BLE chip)
- **Firmware**: CircuitPython
- **Libraries**: `adafruit_ble`, `adafruit_hid`
- **Protocol**: BLE HID over GATT (HOG), Appearance: Generic Keyboard
- **Keycode**: `Keycode.RIGHT_ARROW`
- **Input**: Foot pedal via 1 GPIO pin + GND, internal pull-up resistor

## Key Behavioral Requirements

- Trigger on **release edge** (not press) — send key only when pedal is released
- **10-second cooldown** after each keypress — ignore pedal input during cooldown, no queuing
- **10-minute idle sleep** — disconnect BLE and enter low-power mode; wake on pedal press
- Sleep-to-reconnect latency: 1–2 seconds acceptable
- Debounce chattering from mechanical switch contacts
- Auto-reconnect (bonding) after initial pairing — no iPad Settings intervention needed

## Development Notes

CircuitPython firmware runs as `code.py` on the device's CIRCUITPY drive. Deployment is done by copying files to the mounted USB drive — there is no build/compile step.

To test on device: connect Pico W via USB, copy `code.py` (and any library files under `lib/`) to the CIRCUITPY drive. The REPL is accessible via serial (e.g. `screen /dev/tty.usbmodem* 115200`).

Required libraries (copy to `CIRCUITPY/lib/`):
- `adafruit_ble` bundle
- `adafruit_hid` bundle

Both are available from the [Adafruit CircuitPython Bundle](https://github.com/adafruit/Adafruit_CircuitPython_Bundle).
