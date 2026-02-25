# StockTerminal

Low-overhead, terminal-style stock tracker with a live bar chart and 1984 rainbow Apple-inspired theme.

## Features

- Fast Tkinter desktop app (`stock_terminal_1984.py`)
- Live quote + history refresh
- Multiple graph windows (5m, 10m, 15m, 30m, Hour, 6 Hour, Day, Week, Month, Year)
- Hover crosshair + bar value/time readout
- Drag selection across bars for delta and percent change
- Optional invested-amount tracking (position value + daily P/L)
- Supports stocks, ETFs, and index funds
- Optional Twelve Data API key for improved intraday coverage

## Quick Start (Python)

```bash
python3 stock_terminal_1984.py
```

Optional CLI arguments:

```bash
python3 stock_terminal_1984.py AAPL \
  --logo-side right \
  --refresh 30 \
  --timeout 12 \
  --full-refresh 900 \
  --amount 5000 \
  --twelvedata-key YOUR_KEY
```

## API Key Handling

- No API key is hardcoded in this repository.
- The app asks for a key at launch (optional), or you can pass `--twelvedata-key`.
- Use your own key only;

## Build Scripts

- macOS app and installers:
  - `build_stock_terminal_macos_app.sh`
  - `build_stock_terminal_macos_installers.sh`
- iOS unsigned IPA:
  - `build_stock_terminal_ios_ipa.sh`
- iOS TestFlight pipeline:
  - `build_stock_terminal_ios_testflight.sh`
  - setup details in `TESTFLIGHT_SETUP.md`
- Android APK:
  - `build_stock_terminal_android_apk.sh`
- Windows installer:
  - `build_stock_terminal_windows_installer.sh`

## Project Notes

- Main desktop source: `stock_terminal_1984.py`
- iOS WebView wrapper: `ContentView.swift`
- Android WebView wrapper: `StockTerminalAndroid/app/src/main/assets/stock_terminal.html`

## Credits

- Jack S

