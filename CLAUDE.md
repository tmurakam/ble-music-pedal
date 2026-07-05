# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BLE foot pedal keyboard device for sheet music page-turning. A foot pedal triggers a BLE HID right-arrow keypress sent to an iPad, allowing hands-free page navigation during performance.

## Hardware & Software Stack

- **MCU**: Raspberry Pi Pico W / Pico 2W (CYW43439 BLE chip)
- **Firmware**: CircuitPython (`code.py` — no build step)
- **Libraries**: `adafruit_ble`, `adafruit_hid`
- **Protocol**: BLE HID over GATT (HOG), Appearance `0x03C1` (Generic Keyboard)
- **Keycode**: `Keycode.RIGHT_ARROW`
- **Input**: Foot pedal on `GP15` via internal pull-up (HIGH = open/not pressed, LOW = closed/pressed)

## Deployment

Copy `code.py` and required libraries to the CIRCUITPY USB drive. There is no build or compile step — CircuitPython reloads automatically on file save.

Required libraries (place under `CIRCUITPY/lib/`), available from the Adafruit CircuitPython Bundle:
- `adafruit_ble/`
- `adafruit_hid/`

Serial REPL: `screen /dev/tty.usbmodem* 115200`

## Key Behavioral Requirements

| Requirement | Implementation |
|---|---|
| Trigger on **release edge** only | `raw == True` after `prev_pedal == False` |
| **10-second cooldown** after each send | `cooldown_end = now + COOLDOWN_S`; ignored during cooldown, no queuing |
| **10-minute idle sleep** | `SLEEP_TIMEOUT_S = 600`; disconnect BLE, enter `alarm.light_sleep_until_alarms()` |
| Wake on pedal press | `alarm.pin.PinAlarm(pin=PEDAL_PIN, value=False)` |
| **50 ms debounce** | Confirm stable state after `DEBOUNCE_S` delay |
| Auto-reconnect (bonding) | Handled by `adafruit_ble` BLE stack automatically |

## Architecture

`code.py` is a single-file event loop:

1. **Setup**: create `DigitalInOut` for pedal, init `HIDService` + `BLERadio`, start advertising
2. **Loop**:
   - Check idle timeout → `enter_sleep()` → `alarm.light_sleep_until_alarms()` → restart advertising on wake
   - Read `pedal.value`, debounce on state change
   - On release edge: send `RIGHT_ARROW` if connected and not in cooldown
   - Maintain BLE advertising whenever disconnected

`enter_sleep()` must `deinit()` the `DigitalInOut` before handing the pin to `alarm.pin.PinAlarm`. The pin object is recreated after wake. If the `alarm` module is unavailable (unsupported CircuitPython build), the code falls back to a busy-wait loop.

## Adjustable Constants

```python
PEDAL_PIN = board.GP15   # change to match your wiring
DEBOUNCE_S = 0.05        # increase if you see chatter
COOLDOWN_S = 10.0        # seconds between keypresses
SLEEP_TIMEOUT_S = 600.0  # 10 minutes idle → sleep
```
