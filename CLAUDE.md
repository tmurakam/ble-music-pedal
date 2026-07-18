# CLAUDE.md

このファイルは、このリポジトリでコードを扱う際に Claude Code (claude.ai/code) が参照するガイドである。

## プロジェクト概要

譜めくり用のBLEフットペダル型キーボードデバイス。フットペダルの操作でBLE HIDの右矢印キー押下をiPadに送信し、演奏中に手を使わずページ送りできるようにする。

## ハードウェア・ソフトウェア構成

- **MCU**: Seeed Studio XIAO nRF52840(Nordic nRF52840、BLE 5.0内蔵)
- **ファームウェア**: CircuitPython(`code.py` — ビルド不要)
- **ライブラリ**: `adafruit_ble`、`adafruit_hid`。ペダルのデバウンスは組み込みの`keypad`モジュールを使用(追加ライブラリ不要)
- **プロトコル**: BLE HID over GATT(HOG)、Appearance `0x03C1`(Generic Keyboard)
- **キーコード**: `Keycode.RIGHT_ARROW`
- **入力**: `D0`ピンのフットペダル、内部プルアップ使用(HIGH = 開放/未押下、LOW = 導通/押下)
- **電源**: BAT+ / BAT−パッドに接続したLiPoバッテリー。USB-C経由で充電(基板上の充電回路)

## デプロイ

`code.py` と必要なライブラリをCIRCUITPY USBドライブにコピーする。ビルドやコンパイルの工程はなく、ファイル保存時にCircuitPythonが自動的にリロードする。

必要なライブラリ(`CIRCUITPY/lib/` 配下に配置。Adafruit CircuitPython Bundleから入手可能):
- `adafruit_ble/`
- `adafruit_hid/`

シリアルREPL: `screen /dev/tty.usbmodem* 115200`

## 主要な挙動要件

| 要件 | 実装 |
|---|---|
| **リリースエッジ**でのみトリガー | `keypad.Keys`イベントの`event.released == True` |
| 送信ごとに**10秒間のクールダウン** | `cooldown_end = now + COOLDOWN_S`。クールダウン中は無視し、キューイングもしない |
| **10分間無操作でスリープ** | `SLEEP_TIMEOUT_S = 600`。BLEを切断し`alarm.light_sleep_until_alarms()`に入る |
| ペダル押下でウェイク | `alarm.pin.PinAlarm(pin=PEDAL_PIN, value=False)` |
| **50msデバウンス** | `keypad.Keys`のバックグラウンドスキャンが標準で処理(`KEYPAD_SCAN_INTERVAL_S` × `KEYPAD_DEBOUNCE_THRESHOLD` ≈ `DEBOUNCE_S`)。メインループはデバウンス済みのpress/releaseイベントを読むだけ |
| NCペダルは押下中にプルアップが必要だが、`keypad.Keys(pull=True)`は`value_when_pressed=False`の時しかプルアップを選択しない | `code.py`は`PEDAL_PRESSED`をそのまま渡す代わりに`KEYPAD_VALUE_WHEN_PRESSED = not PEDAL_PRESSED`を`keypad.Keys`に渡し、`_pedal_released()`(`event.released`ではなく`event.pressed`を見る)でkeypad側の反転したイベント名を物理的な意味に変換して読む — 詳細は下記の注意点を参照 |
| 自動再接続(ボンディング) | `adafruit_ble`のBLEスタックが自動処理。手動でのペアリング解除は下記のペダルジェスチャーを参照 |
| **LED点滅: ペアリング中 vs 接続中** | オンボードの青色LED(`board.LED_BLUE`、Low-active)が`LED_BLINK_ON_S`だけ点灯。アドバタイズ中かつ未接続時は`LED_BLINK_PERIOD_PAIRING_S`ごと(0.5秒、2回/秒)、接続中は`LED_BLINK_PERIOD_CONNECTED_S`ごと(3秒に1回)。スリープ中は消灯 |
| **バッテリー残量をホストへ報告** | 標準BLE Battery Service(`0x180F`)を使用。`BATTERY_LOG_INTERVAL_S`(5秒)ごとに`_log_battery()`が`VBATT`をサンプリングし、区分線形の`BATTERY_CURVE`(このセルのデータシート値ではなく一般的な目安のLiPo近似)で電圧→残量%に変換して`BatteryService.level`に書き込む。アドバタイズのペイロードには含まれない(すでにname + appearance + HIDサービスで手一杯) — iOS側は接続後にGATT経由で検出する。充電状態は報告し*ない*(下記の注意点を参照) |
| **ペアリング解除ジェスチャー** | `UNPAIR_WINDOW_S`(5秒)以内に`UNPAIR_TAP_COUNT`(10回)ペダルをリリースすると`_bleio.adapter.erase_bonding()`を呼び、切断・LED高速点滅6回での確認・再アドバタイズを行う。本デバイスにはディスプレイもボタンもないため、古い/不整合なボンディング情報(ホスト側で「登録解除」した後など)をデバイス側からクリアする唯一の手段がこの連打ジェスチャーである。長押しジェスチャーも検討したが却下した — ペダルを踏みっぱなしにするのは通常操作でもあり得るため、意図の識別に使えない。BLE接続状態やクールダウンとは無関係にカウントする。ボンディングが壊れている状況こそこの機能が動く必要がある場面だからである |

## アーキテクチャ

`code.py`は単一ファイルのイベントループである:

1. **セットアップ**: ペダルピン用の`keypad.Keys`を作成し、`HIDService` + `BLERadio`を初期化してアドバタイズを開始
2. **ループ**:
   - アイドルタイムアウトを確認 → `enter_sleep()` → `alarm.light_sleep_until_alarms()` → ウェイク後にアドバタイズ再開
   - 保留中の`pedal.events`を消化(`keypad`によりデバウンス・エッジ検出済み)
   - リリースエッジで: 接続中かつクールダウン外なら`RIGHT_ARROW`を送信
   - 未接続時は常にBLEアドバタイズを維持

`enter_sleep()`は、ピンを`alarm.pin.PinAlarm`に渡す前に`keypad.Keys`オブジェクトを`deinit()`する必要がある。このオブジェクトはウェイク後に再生成される。`alarm`モジュールが利用できない場合(CircuitPythonのビルドが非対応)は、一時的な`DigitalInOut`で生のピンを読むビジーウェイトループ(`_pedal_currently_pressed()`)にフォールバックする。このメソッドは、ウェイク後にペダルが押しっぱなし・スタックしている状態を待つのにも使われる。

### 注意点: `READ_BATT_ENABLE`は常にLOWを維持し、HIGHにしてはならない

Seeed自身のドキュメントで、`board.READ_BATT_ENABLE`(P0.14)をHIGHにすると分圧回路の読み取りパスが無効化され、`board.VBATT`(P0.31、最大入力3.6V)にその上限を超える電圧がかかりうる(特に充電中)、ピン破損のリスクがある、と警告されている。以前のバージョンのコードでは、分圧回路の約2.3uAのリーク電流を節約するために読み取りの合間にHIGHへ切り替えていたが、`code.py`は現在`__init__`内で一度だけセットアップし(`self._batt_enable`)、以降は一切触れない。この安全でない状態に決して陥らないことと引き換えに、わずかな定常リーク(システム全体の消費電力に比べれば無視できる)を受け入れている。

## 調整可能な定数

```python
PEDAL_PIN = board.D0     # 配線に合わせて変更(D0–D10が使用可能)
PEDAL_PRESSED = True     # False = NO(押下で導通)、True = NC(押下で開放) — 下記の注意点を参照
KEYPAD_VALUE_WHEN_PRESSED = not PEDAL_PRESSED  # keypad.Keysに渡すvalue_when_pressed — 下記の注意点を参照
DEBOUNCE_S = 0.05        # デバウンス時間の合計。チャタリングが出る場合は増やす
KEYPAD_SCAN_INTERVAL_S = DEBOUNCE_S / 2   # keypad.Keysのバックグラウンドスキャン間隔
KEYPAD_DEBOUNCE_THRESHOLD = 2             # 確定に必要な一致スキャン回数 ≈ DEBOUNCE_S
MAIN_LOOP_INTERVAL_MS = 20   # メインループの周期。現在はBLE/LEDのハウスキーピングのみをゲートする
COOLDOWN_S = 10.0        # キー送信の間隔(秒)
SLEEP_TIMEOUT_S = 600.0  # 10分間無操作でスリープ
LED_PIN = board.LED_BLUE # アドバタイズ状態表示に使うオンボードRGB LED
LED_BLINK_PERIOD_PAIRING_S = 0.5    # アドバタイズ/ペアリング中の点滅周期(2回/秒)
LED_BLINK_PERIOD_CONNECTED_S = 3.0  # 接続中の点滅周期(3秒に1回)
LED_BLINK_ON_S = 0.1        # 各点滅周期内の点灯時間
BATTERY_LOG_INTERVAL_S = 5.0  # 電圧サンプリング + BLEバッテリー残量更新の頻度
BATTERY_CURVE = (...)         # 区分線形のLiPo 電圧(V) -> 残量% ルックアップテーブル
UNPAIR_TAP_COUNT = 10    # ペアリング解除に必要なペダルリリース回数
UNPAIR_WINDOW_S = 5.0    # そのリリースが収まるべき時間幅(秒)
```

### 注意点: `PEDAL_PRESSED`は単純なNO/NC切り替えスイッチではない

`PEDAL_PRESSED`が完全にNO/NCに依存しない形で扱われているのは`_pedal_currently_pressed()`(生の`digitalio`によるフォールバックポーリング)だけであり、これは常に自前で内部プルアップを有効化し、単純に`PEDAL_PRESSED`と比較している。

他の2箇所は現状のNC-GND配線を前提としており、`PEDAL_PRESSED = False`(GND基準のNOペダル)にそのまま変更すると**安全に動作しない**:

- `keypad.Keys`は内部のプル方向を`value_when_pressed`に紐付けており、プルアップが選択されるのは`value_when_pressed=False`のときだけである。うちのNCペダルは静止時にLOWを出力し、押下時にフロート(プルアップが必要)になるため、`code.py`は反転させた`KEYPAD_VALUE_WHEN_PRESSED`を渡してプルアップを得ており、`_pedal_released()`では`event.pressed`を物理的なリリースエッジとして読んでいる。これは単純な`not PEDAL_PRESSED`という一般式ではない — GND基準のNOペダルもプルアップが必要である(NCペダルとは逆に、押下時ではなく静止時にフロートする)ため、`PEDAL_PRESSED = False`を正しく動作させるには、`KEYPAD_VALUE_WHEN_PRESSED`を無条件に`False`にした上で、NO/NCの区別を`_pedal_released()`側に移す必要がある。
- `_enter_sleep()`内の`alarm.pin.PinAlarm(pin=PEDAL_PIN, value=PEDAL_PRESSED, pull=True)`もおそらく`keypad.Keys`と同じ「値とプル方向が紐付いた」仕様を持つと思われるが、NCケースで正しくプルアップを選択できているかは未検証であり、NOケースはなおさら未検証である。

つまり、NOペダルへの切り替えは`PEDAL_PRESSED`を反転させるだけでは済まず、現状ではこの2箇所の作り直しが必要になる。
