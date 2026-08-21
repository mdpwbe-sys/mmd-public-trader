# 📜 Credits & Intellectual Property Notices

## Open Source Inspirations

**EVE Market Manager** is an independent, clean-room implementation written from scratch in Python 3.10+ and HTML5/CSS3. It draws conceptual inspiration from two pioneering open-source EVE Online market tools:

1. **[Mmd-Project](https://github.com/Slivo-fr/mmd-project)** (by Slivo-fr & Krig Anar)
   - *Inspiration*: Real-time market log export watching and instant Net Margin popover calculation.

2. **[Mmd](https://github.com/slysmoke/mmd)** (by slysmoke / Pete Slattery)
   - *Inspiration*: Multi-character order monitoring, competition analysis, and market data structure design.

---

## Intellectual Property & Clean Room Compliance

- **Clean Implementation**: No source code from Mmd (C++/Qt) or Mmd (C#/WPF) has been copied into this repository. All algorithms, UI components, database schemas, and watcher logic were written independently.
- **Game Formulas & APIs**: All financial calculations (broker fees, sales tax, relist discounts) implement publicly documented CCP game formulas (Viridian 2023 update and CCP March 2026 broker fee rules) and official EVE Online ESI REST API specifications.
- **Data Attributions**: Multi-year market history dataset is aggregated from public EVE Online ESI endpoints and Adam4EVE static price archives (`https://static.adam4eve.eu/`).

---

## CCP Games Trademark Notice

*EVE Online, ESI, the EVE logo, and all associated marks and logos are trademarks or registered trademarks of CCP hf. All rights reserved. EVE Market Manager is an unofficial community tool and is not affiliated with or endorsed by CCP hf.*
