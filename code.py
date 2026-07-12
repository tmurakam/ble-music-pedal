import analogio
import board
import digitalio
import keypad
import time
from adafruit_ble import BLERadio
from adafruit_ble.advertising.standard import ProvideServicesAdvertisement
from adafruit_ble.services.standard.hid import HIDService
from adafruit_ble.services.standard.device_info import DeviceInfoService
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

try:
    import alarm
    SLEEP_SUPPORTED = True
except ImportError:
    SLEEP_SUPPORTED = False

# ── Configuration ──────────────────────────────────────────────────────────────
PEDAL_PIN = board.D0
# Pedal contact type — describes the wiring, GND-referenced in both cases:
#   False = NO (Normally Open,  closes on press) — pin goes LOW  when pressed
#   True  = NC (Normally Closed, opens on press) — pin goes HIGH when pressed
# NOTE: this is only a complete NO/NC switch for _pedal_currently_pressed(), which
# always forces an internal pull-up itself and just compares against this value. The
# keypad.Keys() pull (via KEYPAD_VALUE_WHEN_PRESSED below) and the PinAlarm pull in
# _enter_sleep() are both currently hardcoded for the NC case and would need to be
# reworked to also support flipping this to False.
PEDAL_PRESSED = True
# keypad.Keys ties its internal pull direction to value_when_pressed (pull-up is only
# available when value_when_pressed=False). Our pedal is NC wired to GND: it drives
# LOW at rest and floats when pressed, so it needs a pull-up while pressed. This is
# NOT a generic "not PEDAL_PRESSED" formula — a GND-referenced NO pedal also needs a
# pull-up (it floats at rest instead), so this would need to become unconditionally
# False, with the NO/NC distinction moved into _pedal_released() instead, before
# PEDAL_PRESSED = False would work here again.
KEYPAD_VALUE_WHEN_PRESSED = not PEDAL_PRESSED
DEBOUNCE_S = 0.05        # 50 ms: suppress contact bounce (handled by keypad.Keys)
KEYPAD_SCAN_INTERVAL_S = DEBOUNCE_S / 2  # keypad background scan interval
KEYPAD_DEBOUNCE_THRESHOLD = 2            # confirm after 2 matching scans (= DEBOUNCE_S)
MAIN_LOOP_INTERVAL_MS = 20   # main loop poll interval; debounce/edge detection now
                             # runs in the background via keypad, so this only needs
                             # to be fast enough for BLE/LED housekeeping
COOLDOWN_S = 10.0        # seconds before next keypress is allowed
SLEEP_TIMEOUT_S = 600.0  # 10 minutes idle before BLE disconnect + sleep
HID_DWELL_S = 0.02       # 20 ms key-down time for reliable BLE HID recognition
WAKE_RELEASE_TIMEOUT_S = 5.0  # max wait for pedal release after sleep wake
BATTERY_LOG_INTERVAL_S = 5.0  # how often to sample + print battery voltage
LED_PIN = board.LED_BLUE       # onboard RGB LED, active low
LED_BLINK_PERIOD_PAIRING_S = 0.5    # advertising/pairing: 2 blinks per second
LED_BLINK_PERIOD_CONNECTED_S = 3.0  # connected: 1 blink per 3 seconds
LED_BLINK_ON_S = 0.1                # on-time within each blink cycle


class MusicPedal:
    def __init__(self):
        self._pedal = self._make_pedal()
        print("Pedal GPIO ready")

        self._led = digitalio.DigitalInOut(LED_PIN)
        self._led.direction = digitalio.Direction.OUTPUT
        self._led.value = True  # active low: True = off
        self._led_on = False

        self._charge_rate = digitalio.DigitalInOut(board.CHARGE_RATE)
        self._charge_rate.direction = digitalio.Direction.OUTPUT
        self._charge_rate.value = False  # LOW = 100 mA charge current (default is 50 mA)

        self._hid = HIDService()
        self._device_info = DeviceInfoService(software_revision="1.0", manufacturer="Murakami")
        self._advertisement = ProvideServicesAdvertisement(self._hid)
        self._advertisement.appearance = 961  # 0x03C1: Generic Keyboard
        self._ble = BLERadio()
        self._ble.name = "Murakami BLE Music Pedal"
        self._keyboard = Keyboard(self._hid.devices)

        self._is_advertising = False
        self._was_connected = False
        self._cooldown_end = 0.0
        self._last_activity = time.monotonic()
        self._next_battery_log = 0.0
        print("BLE Music Pedal ready")

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self):
        self._start_advertising()
        while True:
            now = time.monotonic()
            if not self._check_sleep(now):
                self._poll_pedal(now)
                self._update_ble()
                self._update_led(now)
                self._log_battery(now)
            time.sleep(MAIN_LOOP_INTERVAL_MS / 1000)

    # ── Hardware helpers ───────────────────────────────────────────────────────

    def _make_pedal(self):
        # keypad.Keys debounces and edge-detects in the background (C implementation),
        # emitting press/release events instead of requiring manual polling + debounce.
        # See KEYPAD_VALUE_WHEN_PRESSED above for why this isn't PEDAL_PRESSED, and
        # _pedal_released() below for how the resulting inverted event names are
        # translated back to physical meaning.
        return keypad.Keys(
            (PEDAL_PIN,),
            value_when_pressed=KEYPAD_VALUE_WHEN_PRESSED,
            pull=True,
            interval=KEYPAD_SCAN_INTERVAL_S,
            debounce_threshold=KEYPAD_DEBOUNCE_THRESHOLD,
            max_events=4,
        )

    def _pedal_released(self, event):
        # keypad runs with inverted polarity (see _make_pedal above), so its own
        # event.pressed corresponds to the physical release edge.
        return event.pressed

    def _pedal_currently_pressed(self):
        p = digitalio.DigitalInOut(PEDAL_PIN)
        p.direction = digitalio.Direction.INPUT
        p.pull = digitalio.Pull.UP
        pressed = p.value == PEDAL_PRESSED
        p.deinit()
        return pressed

    def _read_battery_voltage(self):
        # Onboard divider (~1/3) is only enabled while READ_BATT_ENABLE is driven
        # low, to avoid its ~2.3uA leakage current the rest of the time.
        enable = digitalio.DigitalInOut(board.READ_BATT_ENABLE)
        enable.direction = digitalio.Direction.OUTPUT
        enable.value = False
        vbat = analogio.AnalogIn(board.VBATT)
        adc_v = (vbat.value / 65535) * vbat.reference_voltage
        vbat.deinit()
        enable.value = True
        enable.deinit()
        return adc_v * 3

    def _log_battery(self, now):
        if now < self._next_battery_log:
            return
        self._next_battery_log = now + BATTERY_LOG_INTERVAL_S
        print(f"Battery: {self._read_battery_voltage():.2f} V")

    # ── BLE helpers ────────────────────────────────────────────────────────────

    def _start_advertising(self):
        self._ble.start_advertising(self._advertisement)
        self._is_advertising = True
        print("Advertising...")

    def _update_led(self, now):
        if self._ble.connected:
            period = LED_BLINK_PERIOD_CONNECTED_S
        elif self._is_advertising:
            period = LED_BLINK_PERIOD_PAIRING_S
        else:
            period = None
        should_light = period is not None and (now % period) < LED_BLINK_ON_S
        if should_light != self._led_on:
            self._led_on = should_light
            self._led.value = not should_light  # active low

    def _enter_sleep(self):
        print("Entering sleep (idle timeout)")
        self._led_on = False
        self._led.value = True  # off during sleep
        if self._is_advertising:
            self._ble.stop_advertising()
            self._is_advertising = False
        for conn in self._ble.connections:
            conn.disconnect()
        time.sleep(0.5)  # let BLE stack finish teardown

        self._pedal.deinit()  # release pin before handing it to the alarm subsystem
        try:                  # always recreate pedal even if sleep setup raises
            if SLEEP_SUPPORTED:
                # Same value-tied-pull convention as keypad.Keys (see
                # KEYPAD_VALUE_WHEN_PRESSED above) — untested whether this correctly
                # gets a pull-up for our NC pedal, or has the same latent bug.
                pin_alarm = alarm.pin.PinAlarm(pin=PEDAL_PIN, value=PEDAL_PRESSED, pull=True)
                alarm.light_sleep_until_alarms(pin_alarm)
            else:
                while not self._pedal_currently_pressed():
                    time.sleep(0.1)
        finally:
            self._pedal = self._make_pedal()
        print("Woke up from sleep")

    def _update_ble(self):
        connected = self._ble.connected
        if connected and not self._was_connected:
            print("BLE connected")
        elif not connected and self._was_connected:
            print("BLE disconnected")
        self._was_connected = connected
        if connected:
            self._is_advertising = False   # BLE stack stops advertising on connect
        elif not self._is_advertising:
            self._start_advertising()      # reconnect after host drops connection

    # ── Pedal logic ────────────────────────────────────────────────────────────

    def _send_key(self, now):
        try:
            self._keyboard.press(Keycode.RIGHT_ARROW)
            time.sleep(HID_DWELL_S)        # 20 ms dwell for reliable HID recognition
            self._keyboard.release_all()
            self._cooldown_end = now + COOLDOWN_S  # only set on successful send (F-03)
            print(f"  Key sent, next after {self._cooldown_end:.1f}s")
        except Exception as e:
            print(f"  Send failed: {e}")   # connection dropped; no cooldown, retry OK

    def _on_pedal_release(self, now):
        print(f"Pedal released at {now:.1f}s")
        if not self._ble.connected:
            print("  Not connected, key skipped")
        elif now < self._cooldown_end:
            print(f"  Cooldown active, key skipped (ready at {self._cooldown_end:.1f}s)")
        else:
            self._send_key(now)

    def _poll_pedal(self, now):
        while True:
            event = self._pedal.events.get()
            if event is None:
                return
            now = time.monotonic()         # re-capture in case of queued events
            self._last_activity = now
            if self._pedal_released(event):
                self._on_pedal_release(now)

    def _check_sleep(self, now):
        if now - self._last_activity < SLEEP_TIMEOUT_S:
            return False
        self._enter_sleep()
        deadline = time.monotonic() + WAKE_RELEASE_TIMEOUT_S
        while self._pedal_currently_pressed() and time.monotonic() < deadline:
            time.sleep(0.01)               # wait for release; timeout guards stuck contact
        self._was_connected = False
        self._last_activity = time.monotonic()
        self._start_advertising()
        return True


MusicPedal().run()
