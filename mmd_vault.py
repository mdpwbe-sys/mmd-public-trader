#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mmd_vault.py - historique des prix dans le vault Obsidian (E:\\EVE\\mmd\\Memorie\\Mmd).

Chaque item a une note:
  Historique/<type_id>.md
  ---
  type_id: 24689
  name: "Rokh Blueprint"
  history:
    - date: 2026-08-06T05:48
      buy_best: 214.6
      sell_best: 210.0
      vol_buy: 1234
      vol_sell: 567
  ---

L'app lit ces notes pour afficher l'evolution d'un item (et declencher des alertes
ex: le prix est tombe sous ton ordre => tu dois baisser).
"""
import os, json, sqlite3, datetime

VAULT = r"E:\EVE\mmd\Memorie\Mmd"
HIST_DIR = os.path.join(VAULT, "Historique")
LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
MAIN_DB = os.path.join(LOCAL, "mmd.com", "Mmd", "db", "main.db")
EVE_DB  = os.path.join(LOCAL, "mmd.com", "Mmd", "resources", "eve.db")
LIVE = "volume_remaining>0 AND last_seen IS NULL"


def _iname(tid):
    con = sqlite3.connect(EVE_DB)
    r = con.execute("SELECT typeName FROM invTypes WHERE typeID=?", (tid,)).fetchone()
    con.close()
    return r[0] if r else f"typeID {tid}"


def _best_prices(tid):
    """Meilleur BUY / SELL public pour un item (toutes stations), depuis external_orders."""
    con = sqlite3.connect(MAIN_DB); cur = con.cursor()
    buy = cur.execute(
        "SELECT MAX(value), SUM(volume_remaining) FROM external_orders WHERE type_id=? AND type=0", (tid,)).fetchone()
    sell = cur.execute(
        "SELECT MIN(value), SUM(volume_remaining) FROM external_orders WHERE type_id=? AND type=1", (tid,)).fetchone()
    con.close()
    return {
        "buy_best": round(buy[0]/1e6, 3) if buy[0] else None,
        "vol_buy": buy[1] or 0,
        "sell_best": round(sell[0]/1e6, 3) if sell[0] else None,
        "vol_sell": sell[1] or 0,
    }


def update_history_from_public(pub):
    """Logge l'historique depuis le livre public EN MEMOIRE (pas de DB Mmd).
    pub: liste [{type_id, location_id, side(0/1), price, issued, vol}]
    Retourne le nb de notes mises a jour."""
    os.makedirs(HIST_DIR, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
    # agrege le meilleur BUY/SELL par item
    best = {}
    for o in pub:
        tid = o["type_id"]
        side = o["side"]
        b = best.setdefault(tid, {"buy_best": 0.0, "vol_buy": 0, "sell_best": 1e18, "vol_sell": 0})
        if side == 0:
            b["buy_best"] = max(b["buy_best"], o["price"])
            b["vol_buy"] += o.get("vol", 0)
        else:
            b["sell_best"] = min(b["sell_best"], o["price"])
            b["vol_sell"] += o.get("vol", 0)
    n = 0
    for tid, p in best.items():
        sb = None if p["sell_best"] >= 1e18 else round(p["sell_best"]/1e6, 3)
        bb = round(p["buy_best"]/1e6, 3) if p["buy_best"] else None
        path = os.path.join(HIST_DIR, f"{tid}.md")
        hist = _read_history(path)
        hist.append({"date": now, "buy_best": bb, "vol_buy": p["vol_buy"],
                     "sell_best": sb, "vol_sell": p["vol_sell"]})
        hist = hist[-30:]
        _write_note(path, tid, _iname(tid), hist)
        n += 1
    return n


def _read_history(path):
    """Lit le front-matter YAML (format simple clé: valeur / listes)."""
    try:
        txt = open(path, encoding="utf-8").read()
        block = txt.split("---")[1] if txt.startswith("---") else ""
        hist = []
        in_h = False
        for line in block.splitlines():
            if line.strip().startswith("- date:"):
                in_h = True
                cur = {}
                cur["date"] = line.split("date:")[1].strip().strip('"')
                hist.append(cur)
            elif in_h and ":" in line and not line.strip().startswith("-"):
                k, v = line.split(":", 1)
                cur = hist[-1]
                cur[k.strip()] = _coerce(v.strip())
        return hist
    except Exception:
        return []


def _coerce(v):
    try: return float(v) if "." in v else int(v)
    except: return v.strip('"')


def _write_note(path, tid, name, hist):
    lines = ["---", f"type_id: {tid}", f'name: "{name}"', "history:"]
    for h in hist:
        lines.append(f'  - date: "{h.get("date","")}"')
        lines.append(f'    buy_best: {h.get("buy_best")}')
        lines.append(f'    vol_buy: {h.get("vol_buy",0)}')
        lines.append(f'    sell_best: {h.get("sell_best")}')
        lines.append(f'    vol_sell: {h.get("vol_sell",0)}')
    lines.append("---")
    lines.append("")
    lines.append(f"# {name}")
    lines.append("")
    lines.append("Historique des prix publics (BUY/SELL best) capture a chaque scan.")
    lines.append("")
    for h in hist[-10:]:
        lines.append(f"- `{h.get('date')}` — BUY top **{h.get('buy_best')}M** ({h.get('vol_buy')} vol) | SELL top **{h.get('sell_best')}M** ({h.get('vol_sell')} vol)")
    open(path, "w", encoding="utf-8").write("\n".join(lines))


def get_item_history(type_id):
    path = os.path.join(HIST_DIR, f"{type_id}.md")
    if not os.path.exists(path):
        return None
    h = _read_history(path)
    name = "?"
    try:
        for line in open(path, encoding="utf-8"):
            if line.startswith("name:"):
                name = line.split(":",1)[1].strip().strip('"')
    except: pass
    return {"type_id": type_id, "name": name, "history": h}


if __name__ == "__main__":
    n = update_history()
    print(f"Vault history: {n} notes mises a jour dans {HIST_DIR}")
