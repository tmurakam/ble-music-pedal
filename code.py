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
DEBOUNCE_S = 0.05        # 50 ms: suppress contact bounce
COOLDOWN_S = 10.0        # seconds before next keypress is allowed
SLEEP_TIMEOUT_S = 600.0  # 10 minutes idle before BLE disconnect + sleep
HID_DWELL_S = 0.02       # 20 ms key-down time for reliable BLE HID recognition
WAKE_RELEASE_TIMEOUT_S = 5.0  # max wait for pedal release after sleep wake


def make_pedal():
    p = digitalio.DigitalInOut(PEDAL_PIN)
    p.direction = digitalio.Direction.INPUT
    p.pull = digitalio.Pull.UP  # HIGH = open (not pressed), LOW = closed (pressed)
    return p


pedal = make_pedal()

# ── BLE HID setup ──────────────────────────────────────────────────────────────
hid = HIDService()
device_info = DeviceInfoService(software_revision="1.0", manufacturer="Murakami")
advertisement = ProvideServicesAdvertisement(hid)
advertisement.appearance = 961  # 0x03C1: Generic Keyboard (BLE Assigned Numbers)
ble = BLERadio()
ble.name = "Murakami BLE Music Pedal"
keyboard = Keyboard(hid.devices)

is_advertising = False


def start_advertising():
    global is_advertising
    ble.start_advertising(advertisement)
    is_advertising = True


def enter_sleep():
    """Disconnect BLE, release GPIO, enter low-power sleep until pedal is pressed."""
    global pedal, is_advertising
    if is_advertising:
        ble.stop_advertising()
        is_advertising = False
    for conn in ble.connections:
        conn.disconnect()
    time.sleep(0.5)  # let BLE stack finish teardown

    pedal.deinit()  # release pin before handing it to the alarm subsystem
    try:            # fix: always recreate pedal even if sleep setup raises
        if SLEEP_SUPPORTED:
            # Wake when pedal pin is pulled LOW (pedal pressed)
            pin_alarm = alarm.pin.PinAlarm(pin=PEDAL_PIN, value=False, pull=True)
            alarm.light_sleep_until_alarms(pin_alarm)
        else:
            # Fallback: busy-wait (no alarm module available)
            p = make_pedal()
            while p.value:
                time.sleep(0.1)
            p.deinit()
    finally:
        pedal = make_pedal()  # always restore, even after exception


# ── Main loop ──────────────────────────────────────────────────────────────────
# True = HIGH = pedal not pressed (internal pull-up)
prev_pedal = True
cooldown_end = 0.0
last_activity = time.monotonic()
start_advertising()

while True:
    now = time.monotonic()

    # ── Idle timeout → sleep (F-08, F-09) ─────────────────────────────────────
    if now - last_activity >= SLEEP_TIMEOUT_S:
        enter_sleep()
        # After wake, wait for pedal release; timeout guards against stuck contact
        deadline = time.monotonic() + WAKE_RELEASE_TIMEOUT_S
        while not pedal.value and time.monotonic() < deadline:
            time.sleep(0.01)
        prev_pedal = True
        last_activity = time.monotonic()
        start_advertising()
        continue

    raw = pedal.value

    # ── Debounce + edge detection (N-06, F-01, F-02) ──────────────────────────
    if raw != prev_pedal:
        time.sleep(DEBOUNCE_S)
        if pedal.value == raw:       # state is stable after debounce period
            now = time.monotonic()   # fix: re-capture after debounce sleep
            prev_pedal = raw
            last_activity = now      # any pedal activity resets sleep timer

            if raw:                  # release edge: LOW → HIGH (pressed → released)
                if now >= cooldown_end and ble.connected:
                    try:             # fix: guard against BLE disconnect race
                        keyboard.press(Keycode.RIGHT_ARROW)
                        time.sleep(HID_DWELL_S)  # fix: 20 ms dwell for reliable HID
                        keyboard.release_all()
                        cooldown_end = now + COOLDOWN_S  # F-03: only on successful send
                    except Exception:
                        pass         # connection dropped; no cooldown, user can retry

    # ── BLE connection maintenance (F-05, F-06) ────────────────────────────────
    if ble.connected:
        is_advertising = False       # BLE stack stops advertising on connect
    elif not is_advertising:
        start_advertising()          # reconnect after host drops connection

    time.sleep(0.005)                # 5 ms polling → <10 ms key latency (N-04)
