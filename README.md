# 🚀 EVE Market Manager (formerly Mmd Order Manager)

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![EVE Online ESI](https://img.shields.io/badge/EVE%20Online-ESI%20API-orange.svg)](https://developers.eveonline.com/)
[![Database](https://img.shields.io/badge/SQLite-WAL%20Mode-green.svg)](https://www.sqlite.org/)

**EVE Market Manager** is a fast, modern desktop application designed for EVE Online market traders. It connects directly to the EVE ESI API and local market export logs to analyze, monitor, and optimize your market orders across multiple characters in real-time.

<img width="466" height="466" alt="EVE Market Manager Visual" src="https://github.com/user-attachments/assets/f25e57e6-7f3d-4537-a0ed-198d6a9d7a17" />

---

## ⚡ Key Features

- **🌐 Multi-Character ESI SSO Authentication**: Connect up to 4+ EVE characters simultaneously via official EVE Online OAuth2 SSO.
- **🎯 Real-Time Competition Analysis**: Instant detection of outbid orders, competitor undercuts, and FIFO queue positioning across regional trade hubs (Jita 4-4, Perimeter citadels).
- **📈 Visual Sparkline Trends & Multi-Series Tracking**: Track historical trends of orders needing update per character or scan mode without graph jump anomalies.
- **💎 Visual Duplicate Item Gallery & Character LED Indicators**: Spot self-competing orders across your alts with unique item icons and colored LED owner indicators:
  - 🔵 **Cyan / Blue**: Char 1
  - 🟠 **Orange**: Char 2
  - 🟣 **Mauve / Purple**: Char 3
  - 🟢 **Green / Emerald**: Char 4+ (Multi-account support)
- **📊 Mmd Project Net Margin & Bulk Batch Engine**: Real-time net profit popover with exact CCP broker fees, accounting taxes, structural station ID matching (Jita 4-4), and Bulk Batch Volume Scaling for commodities like Tritanium.
- **🔒 Mono-Instance Lock & Heartbeat**: Robust PID-aware and health-aware process locking (`psutil` + 5s heartbeat daemon) preventing zombie locks and startup collisions on Windows.
- **💾 SQLite WAL Engine (Zero Data Loss)**: High-concurrency operational store using `BEGIN IMMEDIATE` transactions and automatic exponential backoff retries.
- **📓 Obsidian Vault Mirroring**: Non-blocking derivation layer outputting human-readable Markdown trade journals to your Obsidian vault.

---

## 🛠️ Installation & Setup

### Prerequisites
- **Python 3.10+** (Python 3.14 compatible)
- **Windows 10/11** (uses native EdgeChromium / WebView2 engine)

### Quick Start

1. **Clone the repository**:
   ```bash
   git clone https://github.com/mdpwbe-sys/mmd.git
   cd mmd
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure EVE Online SSO (`.env`)**:
   Copy `.env.example` to `.env` and fill in your CCP Developer credentials:
   ```env
   CLIENT_ID=your_client_id_here
   CLIENT_SECRET=your_client_secret_here
   CALLBACK_URL=http://127.0.0.1:8765/callback
   ```

4. **Launch the application**:
   ```bash
   python mmd_gui.py
   ```
   *or double-click `mmd_gui.bat`.*

---

## 🔐 EVE Online Developer Application Setup

1. Go to the [CCP Developers Portal](https://developers.eveonline.com/).
2. Click **Create New Application**.
3. Set **Connection Type** to `Authentication & API Access`.
4. Add the required Scopes:
   - `esi-ui.open_window.v1`
   - `esi-markets.read_character_orders.v1`
   - `esi-markets.structure_markets.v1`
   - `esi-universe.read_structures.v1`
   - `esi-wallet.read_character_wallet.v1`
   - `esi-markets.read_corporation_orders.v1`
   - `esi-wallet.read_corporation_wallet.v1`
   - `esi-characters.read_standings.v1`
   - `esi-skills.read_skills.v1`
5. Set **Callback URL** to `http://127.0.0.1:8765/callback`.
6. Copy your `Client ID` and `Secret Key` into your local `.env` file.

---

## 🧪 Testing

Run the automated test suite to verify SQLite WAL concurrency, transactional retries, and data integrity:

```bash
python tests_db.py
python test_ticks.py
```

---

## 📄 License & Disclaimer

Distributed under the **MIT License**. See `LICENSE` for details.

*EVE Online and the EVE logo are the registered trademarks of CCP hf. All rights are reserved worldwide. All other trademarks are the property of their respective owners. Thanks to Mmd and Mmd creators for their inspiration.*
