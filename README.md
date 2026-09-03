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
- **🎯 ESI-Cached Competition Analysis (near-real-time)**: Detection of outbid orders, competitor undercuts, and FIFO queue positioning. Market orders are cached by CCP for ~5 minutes, so reads reflect the latest ESI snapshot rather than live ticks.
- **📈 Visual Sparkline Trends & Multi-Series Tracking**: Track historical trends of orders needing update per character or scan mode without graph jump anomalies.
- **💎 Visual Duplicate Item Gallery & Character LED Indicators**: Spot self-competing orders across your alts with unique item icons and colored LED owner indicators:
  - 🔵 **Cyan / Blue**: Char 1
  - 🟠 **Orange**: Char 2
  - 🟣 **Mauve / Purple**: Char 3
  - 🟢 **Green / Emerald**: Char 4+ (Multi-account support)
- **📊 Net Margin & Bulk Batch Engine**: Real-time net profit popover with exact CCP broker fees, accounting taxes, structural station ID matching, and Bulk Batch Volume Scaling for commodities like Tritanium.
- **🏪 Configurable BUY / SELL Stations**: Pick your own BUY station and SELL station per trade (UI picker + direct ID). Faction standing fees resolved dynamically from the selected station — no hardcoded hub.
- **🛰️ Citadel-Aware**: Recognizes Upwell structures. Unresolved citadels (not owned by your corp) show their raw ID and remain filterable — never a crash.
- **🐕 Marketlogs Watchdog**: Watches your EVE `Marketlogs` folder and auto-imports new character/corporation order exports the moment EVE writes them — no manual refresh needed.
- **⚡ Global Hotkeys (navigation)**: System-wide shortcuts that work even when the EVE client or another window is focused:
  - **Alt + Shift + F** → next order in the list
  - **Ctrl + Shift + F** → previous order in the list
- **📋 Fast Copy Price**: copies the selected order's new/recommended price to the clipboard in EVE's decimal format (e.g. `12.345,67`), ready to paste straight into the EVE client's price field.
- **🔒 Mono-Instance Lock & Heartbeat**: Robust process locking preventing zombie locks and startup collisions on Windows.
- **💾 Durable Local Storage (WAL + Atomic Writes)**: High-concurrency operational store using `BEGIN IMMEDIATE` transactions, WAL mode, and automatic backoff retries.
- **🗺️ New Eden 3D Tactical Map**: Explore authentic SDE system positions and real stargate connections in an interactive 3D galaxy, with search, camera focus, security filters, zoom-aware labels, and precise hover selection.
- **🧭 Route Planner & High-Sec Safety**: Set origin/destination directly on the map, inspect every traversed system, receive security warnings, and request a high-sec route that stays at or above 0.5 security where one exists.
- **📡 Cached Live Tactical Intel**: Optional Traffic and Danger overlays use CCP's public ESI jump and kill data with a resilient local cache; animated gate particles visually model estimated link traffic without blocking navigation when ESI is unavailable.
- **🏳️ Influence Overlays**: Display player sovereignty only for eligible null-sec systems, or static Empire & NPC influence, as a separate visual layer over the base security map.
- **💀 Lazy zKillboard Intel**: Opening a system panel can fetch a small, cached set of recent kills on demand, including location, time, value, and an attacker/ship breakdown with links to the external kill reports.
- **🚨 Live Combat Markers**: While the map is open, a bounded zKillboard R2Z2 stream draws short-lived markers for precisely timestamped kills from the last five minutes—without polling every solar system.
- **📍 Opt-in Pilot Position Tracking**: Connected pilots that grant the location scope appear on the map and refresh every 15 seconds while the map is open; selecting one focuses their current system.

---

## 🌌 New Eden Tactical Map

The **New Eden** tab is an interactive navigation and intel view built from bundled SDE geometry: it loads the galaxy and the real stargate graph locally, so the map remains available even when live services are unavailable.

- **Navigation**: search for a system, click to focus it, use right-click to set an origin, double-click to set a destination, or return to the full New Eden view. Region and constellation labels progressively appear at the appropriate zoom level.
- **Security & gates**: high-sec, low-sec, null-sec, and Pochven are visually distinct. Stargate links use a security-aware gradient, making the 0.5 ↔ 0.4 and 0.1 ↔ 0.0 transitions immediately visible.
- **Routes**: the selected route is highlighted system by system with EVE-style square markers. Its summary reports jump count, high-sec safety, traffic, kills, and dangerous systems. A high-sec alternative is offered when the direct route crosses low-sec or null-sec.
- **Live data**: Traffic and Danger are optional overlays sourced server-side from CCP's public ESI endpoints. Results are cached for several minutes and gracefully fall back to the most recent snapshot, so live data never prevents the base map from working.
- **Influence**: Player sovereignty and Empire & NPC overlays retain the normal security colors underneath. The sovereignty layer excludes faction/pirate and non-null systems by design.
- **Pilots**: character markers use the same color LEDs as the dashboard. Grant `esi-location.read_location.v1` during SSO to show a pilot's last ESI location; all markers refresh every 15 seconds only while the map is open.
- **zKillboard**: Recent kills are loaded only after opening a system panel—never for the whole galaxy—and remain cached. Hover a kill for attacker and ship details, then open the external report when needed. While the map is open, the separate R2Z2 stream keeps only precise combat markers from the last five minutes; it is stopped when the map closes.

The visual layer also includes a 360° celestial sky, subtle nebula background, distance-aware system markers, and animated traffic particles. These effects reuse the map's existing canvas render path rather than creating a costly Three.js object per system.

---

## 🛠️ Installation & Setup

### Option A — Run from source (developers)

**Prerequisites:** Python 3.10+ (3.14 compatible), Windows 10/11 (WebView2 / EdgeChromium).

```bash
git clone https://github.com/mdpwbe-sys/mmd-public-trader.git
cd mmd-public-trader
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
2. **First launch wizard**: the app opens a "First launch — EVE configuration" panel.
   Paste **your own** `CLIENT_ID` / `CLIENT_SECRET` (from your CCP developer application).
   The exe ships **no secrets** — each user supplies their own credentials.
3. Click **Save & Connect EVE** → authorizes via CCP → the app populates.

Persistent data (DB, `.env`, logs) lives in `%APPDATA%/MMD-Trader` — so it survives restarts and isn't lost in the temp extraction folder.

> **Requirements on the target machine:** Windows 10/11 with the **WebView2 runtime** (preinstalled on Win11; install once on Win10).

---

## 🔐 EVE Online Developer Application Setup

1. Go to the [CCP Developers Portal](https://developers.eveonline.com/).
2. Click **Create New Application**.
3. Set **Connection Type** to `Authentication & API Access`.
4. Add the required Scopes (must match `mmd_sso.py` exactly):
   - `esi-ui.open_window.v1`
   - `esi-markets.read_character_orders.v1`
   - `esi-markets.structure_markets.v1`
   - `esi-universe.read_structures.v1`
   - `esi-wallet.read_character_wallet.v1`
   - `esi-markets.read_corporation_orders.v1`
   - `esi-wallet.read_corporation_wallets.v1`
   - `esi-assets.read_assets.v1`
   - `esi-assets.read_corporation_assets.v1`
   - `esi-contracts.read_character_contracts.v1`
   - `esi-contracts.read_corporation_contracts.v1`
   - `esi-corporations.read_divisions.v1`
   - `esi-characters.read_blueprints.v1`
   - `esi-characters.read_standings.v1`
   - `esi-skills.read_skills.v1`
   - `esi-location.read_location.v1` *(optional map position tracking)*
5. Set **Callback URL** to `http://127.0.0.1:8766/callback`.
6. Copy your `Client ID` and `Secret Key` into the first-launch wizard (or your local `.env`).

---

## 🏪 Universal Trader Configuration

Unlike the original Mmd (Jita/Perimeter only), this build is hub-agnostic:

- **BUY / SELL station picker** (Settings → Margin panel): search by name (ESI) or paste a station/citadel ID directly.
- **Dynamic faction fees**: broker rate + standing discount are computed from the *selected* station's faction (via SDE), not hardcoded to Caldari.
- **Citadels**: corp-owned structures resolve their name automatically; foreign citadels display their raw `location_id` and stay filterable.
- **Region/station filter**: discoveries from your corporation orders populate the display filter automatically.

---

## 📖 User Guide — How to use the app

### 1. Connect your characters (SSO)
- Click **Connect EVE** (or the SSO button). Your browser opens CCP's login.
- Authorize each character you want to track (up to 4+). Each appears with its own LED color in the UI.
- The app pulls your **personal orders**, **corporation orders**, standings, and wallet automatically via ESI.

### 2. Dashboard — "Orders to Update"
- The **Dashboard** tab shows, per character/filter, the count of orders that need repricing or action.
- **Sparkline**: a small trend graph per filter showing how that count evolved over time (no jump glitches on refresh).
- **Duplicates between characters**: a gallery of items you're selling on multiple alts at competing prices, with color-coded LEDs so you spot self-undercuts instantly.

### 3. FIFO & competition analysis
- The app computes your position in the **FIFO queue** for each order vs competitors and vs your own alts.
- Outbid orders and undercuts are flagged live — you see exactly which orders to fix and by how much.

### 4. Net Margin & price check
- Select an item → a popover shows **net profit** after exact **CCP broker fees + accounting tax**, computed for your configured BUY/SELL stations.
- **Bulk Batch scaling**: for commodities (e.g. Tritanium), it scales the margin across the full volume you intend to move.

### 5. Global hotkeys (navigation)
- **Alt + Shift + F** → jump to the **next** order in the list.
- **Ctrl + Shift + F** → jump to the **previous** order in the list.
- These are system-wide: they work even when the EVE client or any other window is focused (as long as only one app instance is running).

### 6. Fast Copy Price
- Select an order in the list, then use the in-app **Fast Copy** action to copy the **new/recommended price** to the clipboard in EVE's decimal format (e.g. `12.345,67`), ready to paste directly into the EVE client's price field.

### 6. Marketlogs Watchdog (auto-import)
- The **watchdog** monitors your EVE `Documents/EVE/logs/Marketlogs` folder.
- The moment EVE writes a new `My Orders-*.txt` or corporation export, the app imports it automatically — no manual "Refresh" needed.
- Note: `Corporation Orders-*.txt` exports are **excluded** (EVE truncates them); use the ESI corporation-orders pull for those.

### 7. Fees & station configuration
- **Settings → Margin panel**: set your **BUY station** and **SELL station** (search by name or paste the station/citadel ID).
- Broker fee and standing discount are derived from the **selected station's faction** (via the bundled SDE) — no manual rate entry required.
- Foreign citadels you don't own show their raw `location_id`; you can still filter and trade against them.

### 8. Export & history
- **Price history**: daily history per item sourced from ESI public market data and the bundled quickbar reference snapshots.
- **Export**: the app helps you act, but you perform the update yourself — it opens the item's market window via ESI (`esi-ui.open_window`) and **Fast Copy** puts the recommended price on your clipboard; you paste it into the EVE client. You can also export an item deep-dive (potential gain, fees, breakdown) as a self-contained detailed analysis view.

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
