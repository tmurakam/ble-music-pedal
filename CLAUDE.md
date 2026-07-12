# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BLE foot pedal keyboard device for sheet music page-turning. A foot pedal triggers a BLE HID right-arrow keypress sent to an iPad, allowing hands-free page navigation during performance.

## Hardware & Software Stack

- **MCU**: Seeed Studio XIAO nRF52840 (Nordic nRF52840, BLE 5.0 native)
- **Firmware**: CircuitPython (`code.py` — no build step)
- **Libraries**: `adafruit_ble`, `adafruit_hid`; pedal debouncing uses the built-in `keypad` module (no extra library needed)
- **Protocol**: BLE HID over GATT (HOG), Appearance `0x03C1` (Generic Keyboard)
- **Keycode**: `Keycode.RIGHT_ARROW`
- **Input**: Foot pedal on `D0` via internal pull-up (HIGH = open/not pressed, LOW = closed/pressed)
- **Power**: LiPo battery connected to BAT+ / BAT− pads; charges via USB-C (onboard charging circuit)

## Deployment

Copy `code.py` and required libraries to the CIRCUITPY USB drive. There is no build or compile step — CircuitPython reloads automatically on file save.

Required libraries (place under `CIRCUITPY/lib/`), available from the Adafruit CircuitPython Bundle:
- `adafruit_ble/`
- `adafruit_hid/`

Serial REPL: `screen /dev/tty.usbmodem* 115200`

## Key Behavioral Requirements

| Requirement | Implementation |
|---|---|
| Trigger on **release edge** only | `keypad.Keys` event with `event.released == True` |
| **10-second cooldown** after each send | `cooldown_end = now + COOLDOWN_S`; ignored during cooldown, no queuing |
| **10-minute idle sleep** | `SLEEP_TIMEOUT_S = 600`; disconnect BLE, enter `alarm.light_sleep_until_alarms()` |
| Wake on pedal press | `alarm.pin.PinAlarm(pin=PEDAL_PIN, value=False)` |
| **50 ms debounce** | Handled natively by `keypad.Keys` background scanning (`KEYPAD_SCAN_INTERVAL_S` × `KEYPAD_DEBOUNCE_THRESHOLD` ≈ `DEBOUNCE_S`); the main loop only reads already-debounced press/release events |
| NC pedal needs a pull-up while pressed, but `keypad.Keys(pull=True)` only selects a pull-up when `value_when_pressed=False` | `code.py` passes `KEYPAD_VALUE_WHEN_PRESSED = not PEDAL_PRESSED` to `keypad.Keys` instead of `PEDAL_PRESSED` directly, then reads `_pedal_released()` (which checks `event.pressed`, not `event.released`) to translate keypad's resulting inverted event names back to physical meaning — see caveat below |
| Auto-reconnect (bonding) | Handled by `adafruit_ble` BLE stack automatically |
| **LED blink: pairing vs connected** | Onboard blue LED (`board.LED_BLUE`, active low) flashes on for `LED_BLINK_ON_S`; every `LED_BLINK_PERIOD_PAIRING_S` (0.5s, 2×/s) while advertising and not connected, every `LED_BLINK_PERIOD_CONNECTED_S` (3s) while connected; off when asleep |

## Architecture

`code.py` is a single-file event loop:

1. **Setup**: create `keypad.Keys` for the pedal pin, init `HIDService` + `BLERadio`, start advertising
2. **Loop**:
   - Check idle timeout → `enter_sleep()` → `alarm.light_sleep_until_alarms()` → restart advertising on wake
   - Drain pending `pedal.events` (already debounced/edge-detected by `keypad`)
   - On release edge: send `RIGHT_ARROW` if connected and not in cooldown
   - Maintain BLE advertising whenever disconnected

`enter_sleep()` must `deinit()` the `keypad.Keys` object before handing the pin to `alarm.pin.PinAlarm`. The object is recreated after wake. If the `alarm` module is unavailable (unsupported CircuitPython build), the code falls back to a busy-wait loop that reads the raw pin via a temporary `DigitalInOut` (`_pedal_currently_pressed()`), which is also used to wait out a stuck/held pedal after waking.

## Adjustable Constants

```python
PEDAL_PIN = board.D0     # change to match your wiring (D0–D10 available)
PEDAL_PRESSED = True     # False = NO (closes on press), True = NC (opens on press) — see caveat below
KEYPAD_VALUE_WHEN_PRESSED = not PEDAL_PRESSED  # value_when_pressed passed to keypad.Keys — see caveat below
DEBOUNCE_S = 0.05        # total debounce time; increase if you see chatter
KEYPAD_SCAN_INTERVAL_S = DEBOUNCE_S / 2   # keypad.Keys background scan interval
KEYPAD_DEBOUNCE_THRESHOLD = 2             # matching scans required ≈ DEBOUNCE_S
MAIN_LOOP_INTERVAL_MS = 20   # main loop tick; only gates BLE/LED housekeeping now
COOLDOWN_S = 10.0        # seconds between keypresses
SLEEP_TIMEOUT_S = 600.0  # 10 minutes idle → sleep
LED_PIN = board.LED_BLUE # onboard RGB LED used for advertising indicator
LED_BLINK_PERIOD_PAIRING_S = 0.5    # blink cycle while advertising/pairing (2x/s)
LED_BLINK_PERIOD_CONNECTED_S = 3.0  # blink cycle while connected (1x/3s)
LED_BLINK_ON_S = 0.1        # on-time within each blink cycle
```

### Caveat: `PEDAL_PRESSED` is not a clean NO/NC toggle

`PEDAL_PRESSED` is fully NO/NC-agnostic only in `_pedal_currently_pressed()` (the raw
`digitalio` fallback poll), which always forces an internal pull-up itself and simply
compares against `PEDAL_PRESSED`.

The other two consumers assume the current NC-to-GND wiring and are **not** safe to use
with `PEDAL_PRESSED = False` (a GND-referenced NO pedal) as-is:

- `keypad.Keys` ties its internal pull direction to `value_when_pressed`: a pull-up is
  only selected when `value_when_pressed=False`. Our NC pedal drives LOW at rest and
  floats (needing a pull-up) when pressed, so `code.py` passes the inverted
  `KEYPAD_VALUE_WHEN_PRESSED` to get that pull-up, and reads `event.pressed` in
  `_pedal_released()` as the physical release edge. This is not a generic
  `not PEDAL_PRESSED` formula — a GND-referenced NO pedal also needs a pull-up (it
  floats at rest instead of when pressed), so `KEYPAD_VALUE_WHEN_PRESSED` would need to
  become unconditionally `False`, with the NO/NC distinction moved into
  `_pedal_released()`, before `PEDAL_PRESSED = False` would work correctly here.
- `alarm.pin.PinAlarm(pin=PEDAL_PIN, value=PEDAL_PRESSED, pull=True)` in `_enter_sleep()`
  likely has the same value-tied-pull convention as `keypad.Keys` and has not been
  verified to correctly select a pull-up for the NC case, let alone NO.

In short: switching to a NO pedal today requires reworking both of these, not just
flipping `PEDAL_PRESSED`.
