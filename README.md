# 🚀 EVE Market Manager (formerly Mmd Order Manager)

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![EVE Online ESI](https://img.shields.io/badge/EVE%20Online-ESI%20API-orange.svg)](https://developers.eveonline.com/)
[![Database](https://img.shields.io/badge/SQLite-WAL%20Mode-green.svg)](https://www.sqlite.org/)

**EVE Market Manager** is a fast, modern desktop application designed for EVE Online market traders. It connects directly to the EVE ESI API and local market export logs to analyze, monitor, and optimize your market orders across multiple characters in real-time.

This is the **universal trader** build: any region, any station, any citadel — not tied to a single trade hub.

<img width="466" height="466" alt="EVE Market Manager Visual" src="https://github.com/user-attachments/assets/f25e57e6-7f3d-4537-a0ed-198d6a9d7a17" />

---

## ⚡ Key Features

- **🌐 Multi-Character ESI SSO Authentication**: Connect up to 4+ EVE characters simultaneously via official EVE Online OAuth2 SSO.
- **🎯 Real-Time Competition Analysis**: Instant detection of outbid orders, competitor undercuts, and FIFO queue positioning across regional trade hubs.
- **📈 Visual Sparkline Trends & Multi-Series Tracking**: Track historical trends of orders needing update per character or scan mode without graph jump anomalies.
- **💎 Visual Duplicate Item Gallery & Character LED Indicators**: Spot self-competing orders across your alts with unique item icons and colored LED owner indicators:
  - 🔵 **Cyan / Blue**: Char 1
  - 🟠 **Orange**: Char 2
  - 🟣 **Mauve / Purple**: Char 3
  - 🟢 **Green / Emerald**: Char 4+ (Multi-account support)
- **📊 Net Margin & Bulk Batch Engine**: Real-time net profit popover with exact CCP broker fees, accounting taxes, structural station ID matching, and Bulk Batch Volume Scaling for commodities like Tritanium.
- **🏪 Configurable BUY / SELL Stations**: Pick your own BUY station and SELL station per trade (UI picker + direct ID). Faction standing fees resolved dynamically from the selected station — no hardcoded hub.
- **🛰️ Citadel-Aware**: Recognizes Upwell structures. Unresolved citadels (not owned by your corp) show their raw ID and remain filterable — never a crash.
- **🔒 Mono-Instance Lock & Heartbeat**: Robust process locking preventing zombie locks and startup collisions on Windows.
- **💾 SQLite WAL Engine (Zero Data Loss)**: High-concurrency operational store using `BEGIN IMMEDIATE` transactions and automatic backoff retries.
- **📓 Obsidian Vault Mirroring**: Non-blocking Markdown trade journals to your Obsidian vault.

---

## 🛠️ Installation & Setup

### Option A — Run from source (developers)

**Prerequisites:** Python 3.10+ (3.14 compatible), Windows 10/11 (WebView2 / EdgeChromium).

```bash
git clone https://github.com/mdpwbe-sys/mmd.git
cd mmd
pip install -r requirements.txt
python mmd_gui.py
# or double-click mmd_gui.bat
```

### Option B — Standalone executable (non-developers)

A single `.exe` with everything bundled. No Python install required.

1. Build it (or grab a release):
   ```bat
   build_exe.bat
   ```
   → produces `dist/MMD-Trader.exe`.
2. **First launch wizard**: the app opens a "1er lancement — configuration EVE" panel.
   Paste **your own** `CLIENT_ID` / `CLIENT_SECRET` (from your CCP developer application).
   The exe ships **no secrets** — each user supplies their own credentials.
3. Click **Sauver & Connecter EVE** → authorizes via CCP → the app populates.

Persistent data (DB, `.env`, logs) lives in `%APPDATA%/MMD-Trader` — so it survives restarts and isn't lost in the temp extraction folder.

> **Requirements on the target machine:** Windows 10/11 with the **WebView2 runtime** (preinstalled on Win11; install once on Win10).

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
6. Copy your `Client ID` and `Secret Key` into the first-launch wizard (or your local `.env`).

---

## 🏪 Universal Trader Configuration

Unlike the original Mmd (Jita/Perimeter only), this build is hub-agnostic:

- **BUY / SELL station picker** (Settings → Margin panel): search by name (ESI) or paste a station/citadel ID directly.
- **Dynamic faction fees**: broker rate + standing discount are computed from the *selected* station's faction (via SDE), not hardcoded to Caldari.
- **Citadels**: corp-owned structures resolve their name automatically; foreign citadels display their raw `location_id` and stay filterable.
- **Region/station filter**: discoveries from your corporation orders populate the display filter automatically.

---

## 🧪 Testing

```bash
python tests_db.py
python test_ticks.py
```

---

## 📄 License & Disclaimer

Distributed under the **MIT License**. See `LICENSE` for details.

*EVE Online and the EVE logo are the registered trademarks of CCP hf. All rights are reserved worldwide. All other trademarks are the property of their respective owners. Thanks to Mmd and Mmd creators for their inspiration.*
