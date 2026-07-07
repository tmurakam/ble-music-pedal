import board
import digitalio
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
# Pedal contact type (change to match your hardware):
#   False = NO (Normally Open,  closes on press) — pin goes LOW  when pressed
#   True  = NC (Normally Closed, opens on press) — pin goes HIGH when pressed
PEDAL_PRESSED = False
DEBOUNCE_S = 0.05        # 50 ms: suppress contact bounce
COOLDOWN_S = 10.0        # seconds before next keypress is allowed
SLEEP_TIMEOUT_S = 600.0  # 10 minutes idle before BLE disconnect + sleep
HID_DWELL_S = 0.02       # 20 ms key-down time for reliable BLE HID recognition
WAKE_RELEASE_TIMEOUT_S = 5.0  # max wait for pedal release after sleep wake
LED_PIN = board.LED_BLUE       # onboard RGB LED, active low
LED_BLINK_PERIOD_S = 2.0       # blink cycle length while advertising (pairing/reconnecting)
LED_BLINK_ON_S = 0.1           # on-time within each blink cycle


class MusicPedal:
    def __init__(self):
        self._pedal = self._make_pedal()
        print("Pedal GPIO ready")

        self._led = digitalio.DigitalInOut(LED_PIN)
        self._led.direction = digitalio.Direction.OUTPUT
        self._led.value = True  # active low: True = off
        self._led_on = False

        self._hid = HIDService()
        self._device_info = DeviceInfoService(software_revision="1.0", manufacturer="Murakami")
        self._advertisement = ProvideServicesAdvertisement(self._hid)
        self._advertisement.appearance = 961  # 0x03C1: Generic Keyboard
        self._ble = BLERadio()
        self._ble.name = "Murakami BLE Music Pedal"
        self._keyboard = Keyboard(self._hid.devices)

        self._is_advertising = False
        self._was_connected = False
        self._prev_pedal = not PEDAL_PRESSED  # initial state: pedal at rest
        self._cooldown_end = 0.0
        self._last_activity = time.monotonic()
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
            time.sleep(0.005)              # 5 ms polling → <10 ms key latency (N-04)

    # ── Hardware helpers ───────────────────────────────────────────────────────

    def _make_pedal(self):
        p = digitalio.DigitalInOut(PEDAL_PIN)
        p.direction = digitalio.Direction.INPUT
        p.pull = digitalio.Pull.UP  # HIGH = open (not pressed), LOW = closed (pressed)
        return p

    # ── BLE helpers ────────────────────────────────────────────────────────────

    def _start_advertising(self):
        self._ble.start_advertising(self._advertisement)
        self._is_advertising = True
        print("Advertising...")

    def _update_led(self, now):
        should_light = (
            self._is_advertising
            and not self._ble.connected
            and (now % LED_BLINK_PERIOD_S) < LED_BLINK_ON_S
        )
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
                pin_alarm = alarm.pin.PinAlarm(pin=PEDAL_PIN, value=PEDAL_PRESSED, pull=True)
                alarm.light_sleep_until_alarms(pin_alarm)
            else:
                p = self._make_pedal()
                while p.value:
                    time.sleep(0.1)
                p.deinit()
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
        raw = self._pedal.value
        if raw == self._prev_pedal:
            return
        time.sleep(DEBOUNCE_S)
        if self._pedal.value != raw:
            return                         # bounced, ignore
        now = time.monotonic()             # re-capture after debounce sleep (F-05 fix)
        self._prev_pedal = raw
        self._last_activity = now
        if raw != PEDAL_PRESSED:           # release edge: active → rest
            self._on_pedal_release(now)

    def _check_sleep(self, now):
        if now - self._last_activity < SLEEP_TIMEOUT_S:
            return False
        self._enter_sleep()
        deadline = time.monotonic() + WAKE_RELEASE_TIMEOUT_S
        while self._pedal.value == PEDAL_PRESSED and time.monotonic() < deadline:
            time.sleep(0.01)               # wait for release; timeout guards stuck contact
        self._prev_pedal = True
        self._was_connected = False
        self._last_activity = time.monotonic()
        self._start_advertising()
        return True


MusicPedal().run()
