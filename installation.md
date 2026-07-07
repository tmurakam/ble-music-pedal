# セットアップ手順 — BLE フットペダル (XIAO nRF52840)

CircuitPython の書き込みが完了している状態から、デバイスを動作させるまでの手順。

## 前提

- [ ] Seeed Studio XIAO nRF52840 に CircuitPython (10.2.1系) 書き込み済み
- [ ] `wiring.md` の配線が完了している(フットペダル、LiPoバッテリー)

## 手順

### 1. CIRCUITPY ドライブの確認

XIAO を USB-C でPCに接続すると、`CIRCUITPY` という名前のドライブがマウントされる。

### 2. Adafruit CircuitPython Bundle の入手

[circuitpython.org/libraries](https://circuitpython.org/libraries) から、書き込み済みの CircuitPython バージョンに対応する Bundle をダウンロードする。

`requirements.txt` に記載のバージョンに対応するものを選ぶ:

| ライブラリ | バージョン |
|---|---|
| adafruit-circuitpython-ble | 10.1.3 |
| adafruit-circuitpython-hid | 6.1.10 |

### 3. ライブラリのコピー

ダウンロードした Bundle 内から、以下のフォルダを丸ごと `CIRCUITPY/lib/` にコピーする。

- `lib/adafruit_ble/`
- `lib/adafruit_hid/`

### 4. code.py のコピー

このリポジトリの `code.py` を `CIRCUITPY/` 直下にコピーする。保存すると CircuitPython が自動的にリロードする。

### 5. 動作確認

1. iPad の Bluetooth 設定を開き、新しいキーボードデバイスとしてペアリングする
2. 必要であればシリアル REPL でログを確認する

   ```
   screen /dev/tty.usbmodem* 115200
   ```

3. ペダルを踏んで離し、リリースエッジで右矢印キーが送信されることを確認する
4. 踏みっぱなしでは追加送信が起きないこと、送信後10秒間は再送信されない(クールダウン)ことを確認する
5. 10分間操作しない状態でスリープに移行し、ペダルを踏むと復帰・再接続することを確認する

## トラブルシューティング

| 症状 | 確認ポイント |
|---|---|
| CIRCUITPY がマウントされない | USBケーブルがデータ通信対応か確認(充電専用ケーブルでは不可) |
| iPad にデバイスが表示されない | `lib/` にライブラリが正しく配置されているか、REPL でエラーが出ていないか確認 |
| キーが送信されない | 配線(D0–GND)、`PEDAL_PRESSED` の設定がペダルの種類(NO/NC)と合っているか確認 |
| ペアリング情報が引き継がれない | iPad側の Bluetooth 設定でデバイスを一度削除し、再ペアリングを試す |
