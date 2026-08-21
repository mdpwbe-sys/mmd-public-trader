#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Calcul de marge nette style Mmd Project, hors frais de trade calcules
via les standings/skills du perso (formules CCP officielles 2026-03-02).

Source des formules : CCP Broker Fee & Sales Tax (maj 2026-03-02),
Viridian (Upwell 0.5% SCC), Grand Heist (Advanced Broker Relations 6%/niv).

Le popup est declenche par l'export d'un item : 'The Forge-<item>*.txt'
(c'est un LIVRE PUBLIC, pas un export d'ordres perso). On ne l'importe PAS
comme des ordres -> on calcule la marge nette et on l'affiche.

Config (standings/skills) lue depuis broker_config.json (source de verite)
et mise en miroir dans la note Obsidian EVE/EVE-Broker-Fees.md.
"""
from decimal import Decimal, getcontext
import os, json

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "broker_config.json")
OBSIDIAN_NOTE = r"G:/HERMES_MEMORIES/HERMES_MERMORIES/EVE/EVE-Broker-Fees.md"

# cache des labels faction/corp (ESI universe/names, anonyme)
_LABEL_CACHE = {}


def _label_for_id(faction_or_corp_id):
    """Nom lisible d'une faction/corp depuis ESI /universe/names/ (anonyme, pas de scope)."""
    fid = int(faction_or_corp_id or 0)
    if not fid:
        return None
    if fid in _LABEL_CACHE:
        return _LABEL_CACHE[fid]
    try:
        import urllib.request, urllib.error, json as _json
        url = "https://esi.evetech.net/v3/universe/names/?datasource=tranquility"
        req = urllib.request.Request(url, data=_json.dumps([fid]).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = _json.loads(r.read().decode("utf-8"))
        name = data[0]["name"] if data else None
        _LABEL_CACHE[fid] = name
        return name
    except Exception:
        _LABEL_CACHE[fid] = None
        return None

# ---- constantes CCP (2026) ----
NPC_BASE_BROKER_RATE = Decimal("0.03")
BROKER_RELATIONS_REDUCTION = Decimal("0.003")      # par niveau BR
FACTION_STANDING_REDUCTION = Decimal("0.0003")      # par point de standing faction
CORPORATION_STANDING_REDUCTION = Decimal("0.0002")  # par point de standing corp
NPC_MIN_BROKER_RATE = Decimal("0.01")
UPWELL_SCC_SURCHARGE = Decimal("0.005")
BASE_RELIST_DISCOUNT = Decimal("0.50")
ABR_RELIST_DISCOUNT_PER_LEVEL = Decimal("0.06")
MINIMUM_BROKER_CHARGE_ISK = Decimal("100")
BASE_SALES_TAX = Decimal("0.075")
ACCOUNTING_REDUCTION = Decimal("0.11")              # 11% du taux de base par niveau

# stations NPC connues -> (nom faction, nom corporation proprietaire)
NPC_STATIONS = {
    60003760: ("Caldari State", "Caldari Navy"),   # Jita IV - Moon 4
    60008494: ("Caldari State", "Caldari Navy"),   # Jita IV - Moon 4 (autre)
    60004588: ("Caldari State", "Caldari Navy"),   # Jita IV - Moon 4
    10000002: ("Caldari State", "Caldari Navy"),   # fallback region The Forge
}

DEFAULT_CONFIG = {
    "broker_relations": 0,
    "advanced_broker_relations": 0,
    "accounting": 0,
    "standings": {},   # mapping dynamique faction/corp -> standing brut (rempli par fetch_esi_config)
    "upwell_owner_fee": 0.0,
    "buy_station": 0,   # 0 = auto-deduit des ordres (station la plus frequente cote BUY)
    "sell_station": 0,  # 0 = auto-deduit des ordres (station la plus frequente cote SELL)
    "item_tax": 0.0,   # taxe specific item (0 par defaut)
}


def load_config():
    """Lit broker_config.json (source de verite). Fusionne avec les defauts."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
        if isinstance(cfg.get("standings"), dict):
            cfg["standings"] = {**DEFAULT_CONFIG["standings"], **cfg["standings"]}
    except Exception:
        pass
    return cfg


def save_config(cfg):
    """Ecrit broker_config.json + met a jour la note Obsidian miroir."""
    cfg = {k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG}
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    _write_obsidian_note(cfg)
    return cfg


def _write_obsidian_note(cfg):
    """Miroir memoire Obsidian : formules + config courante du perso."""
    st = cfg.get("standings", {})
    cs = st.get("Caldari State", 0.0)
    cn = st.get("Caldari Navy", 0.0)
    rate = npc_broker_rate(cfg["broker_relations"], Decimal(str(cs)), Decimal(str(cn)))
    tax = sales_tax_rate(cfg["accounting"])
    note = f"""# EVE - Broker Fees & Sales Tax (config perso)

> Source CCP (maj 2026-03-02) : Broker Fee and Sales Tax, Viridian (Upwell),
> Grand Heist (Advanced Broker Relations 6%/niv, max 80% a V).

## Config active (utilisee par le calcul de marge nette)

| Parametre | Valeur | Effet |
|-----------|--------|-------|
| Broker Relations (BR) | {cfg['broker_relations']} | -{cfg['broker_relations']*0.3:.2f} pt |
| Advanced Broker Relations (ABR) | {cfg['advanced_broker_relations']} | relist -{cfg['advanced_broker_relations']*6}% |
| Accounting | {cfg['accounting']} | sales tax -{cfg['accounting']*11}% |
| Standing Caldari State (brut) | {cs} | -{cs*0.03:.2f} pt |
| Standing Caldari Navy (brut) | {cn} | -{cn*0.02:.2f} pt |
| Upwell owner fee | {cfg['upwell_owner_fee']}% | SCC 0.5% + ca |
| Station defaut | {cfg['default_station']} | Jita IV-4 (Caldari) |

**Taux calcule (Jita, NPC) :** broker = {rate:.4f} | sales tax = {tax:.4f}

## Formules (Decimal, jamais float)

### Station NPC (Jita)
```
broker = max(1%, 3% - 0.3%*BR - 0.03%*faction - 0.02%*corp)
sales_tax = 7.5% * (1 - 0.11*Accounting)
```

### Citadelle Upwell
```
broker = 0.5% (SCC) + owner_fee
```

### Relist (modif ordre)
```
RD = 50% + 6%*ABR   (max 80% a V)
fee = max(100 ISK, max(0, BR*(P2-P1)) + (1-RD)*BR*P2)
```

## Marge nette (Mmd-style)
- Prix achat = min(prix SELL public)
- Prix vente = max(prix BUY public)
- Vol tradable = min(vol du meilleur BUY, vol du meilleur SELL)
- Frais/unit : broker achat (sur prix achat) + sales tax (sur prix vente)
  + broker vente (sur prix vente)
- Marge nette/unit = prix vente - prix achat - frais/unit
"""
    try:
        os.makedirs(os.path.dirname(OBSIDIAN_NOTE), exist_ok=True)
        with open(OBSIDIAN_NOTE, "w", encoding="utf-8") as f:
            f.write(note)
    except Exception:
        pass


# ---- formules CCP (Decimal) ----
def npc_broker_rate(broker_relations_level, faction_standing, corporation_standing):
    rate = (NPC_BASE_BROKER_RATE
            - BROKER_RELATIONS_REDUCTION * Decimal(broker_relations_level)
            - FACTION_STANDING_REDUCTION * Decimal(faction_standing)
            - CORPORATION_STANDING_REDUCTION * Decimal(corporation_standing))
    return max(NPC_MIN_BROKER_RATE, rate)


def upwell_broker_rate(owner_fee_pct):
    return UPWELL_SCC_SURCHARGE + Decimal(str(owner_fee_pct)) / Decimal("100")


def sales_tax_rate(accounting_level):
    return BASE_SALES_TAX * (Decimal("1") - ACCOUNTING_REDUCTION * Decimal(accounting_level))


def relist_discount(advanced_broker_relations_level):
    return BASE_RELIST_DISCOUNT + ABR_RELIST_DISCOUNT_PER_LEVEL * Decimal(advanced_broker_relations_level)


def broker_fee(order_value, broker_rate):
    """max(100 ISK, order_value * broker_rate). order_value = prix * vol."""
    return max(MINIMUM_BROKER_CHARGE_ISK, order_value * broker_rate)


def station_kind(station_id):
    """Retourne ('npc', faction, corp) ou ('upwell', None, None) selon les règles structurelles CCP."""
    s_id = int(station_id or 0)
    if s_id in NPC_STATIONS:
        fac, corp = NPC_STATIONS[s_id]
        return ("npc", fac, corp)
    # Règle structurelle CCP officielle:
    # 60 000 000 <= station_id < 64 000 000 = Station NPC
    if 60000000 <= s_id < 64000000:
        return ("npc", "Caldari State", "Caldari Navy")
    # station_id >= 10^12 = Structure / Citadelle Upwell
    if s_id >= 1000000000000:
        return ("upwell", None, None)
    # Par défaut pour les stations NPC classiques
    return ("npc", "Caldari State", "Caldari Navy")


def rate_for_station(station_id, cfg):
    kind, fac, corp = station_kind(station_id)
    if kind == "npc":
        st = cfg.get("standings", {})
        return npc_broker_rate(cfg["broker_relations"],
                              Decimal(str(st.get(fac, 0.0))),
                              Decimal(str(st.get(corp, 0.0))))
    return upwell_broker_rate(cfg.get("upwell_owner_fee", 0.0))


# ---- parsing du livre public (The Forge-<item>.txt) ----
def parse_market_book(path):
    """Parse un livre public EVE ('The Forge-<item>*.txt').
    Retourne (rows, type_id, item_name) ou leve ValueError.
    rows: list de {price: Decimal, vol: int, side: 0/1, station_id: int}
    Le header n'a PAS orderState/charID (c'est un livre public).
    """
    import csv
    if not os.path.exists(path):
        raise ValueError("fichier introuvable")
    rows = []
    tid = None
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "price" not in reader.fieldnames:
            raise ValueError("format de livre public inconnu (entete price absente)")
        for row in reader:
            try:
                price = Decimal(str(float(row["price"])))
                vol = int(float(row.get("volRemaining", "0") or 0))
                bid = str(row.get("bid", "")).strip().lower() == "true"
                s = int(float(row.get("stationID", "0") or 0))
            except (KeyError, ValueError, TypeError):
                continue
            if tid is None and row.get("typeID"):
                try:
                    tid = int(float(row["typeID"]))
                except (ValueError, TypeError):
                    pass
            rows.append({"price": price, "vol": vol, "side": 0 if bid else 1, "station_id": s})
    if not rows:
        raise ValueError("livre public vide")
    name = _iname(tid) if tid else "item inconnu"
    return rows, tid, name


def _iname(tid):
    try:
        import sqlite3
        con = sqlite3.connect(os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "mmd.com", "Mmd",
            "resources", "eve.db"))
        r = con.execute("SELECT typeName FROM invTypes WHERE typeID=?", (tid,)).fetchone()
        con.close()
        return r[0] if r else f"typeID {tid}"
    except Exception:
        return f"typeID {tid}"


# ---- calcul de marge nette ----
def compute_margin(rows, cfg, station_pref=None, order_station_id=None):
    """Calcule la marge nette Mmd-style a partir d'un livre public.

    IMPORTANT (fix bug 2026-08-10) : la marge depend de OU on achete ET ou on vend.
    - station cible de VENTE = station_pref / Jita 4-4 (60003760) / station majoritaire
    - station d'ACHAT = order_station_id (ou la station cible si non precise)
      -> si l'ordre est a Perimeter et qu'on vend a Jita, le BUY utilise le prix
         de Perimeter, pas le meilleur BUY Jita (sinon marge fausse en %+ enorme).
    - Applique le plancher de 100 ISK par ORDRE TOTAL (et non par unite).
    """
    if not rows:
        return {"ok": False, "reason": "livre public vide"}

    # Determiner la station cible de VENTE
    available_stations = set(r["station_id"] for r in rows if r.get("station_id"))
    target_station = None
    sell_cfg = cfg.get("sell_station") or 0
    buy_cfg = cfg.get("buy_station") or 0
    if sell_cfg and sell_cfg in available_stations:
        target_station = sell_cfg
    elif available_stations:
        # fallback : station majoritaire parmi les SELL du livre
        st_counts = {}
        for r in rows:
            if r.get("side") == 1:
                st = r.get("station_id")
                if st:
                    st_counts[st] = st_counts.get(st, 0) + 1
        if st_counts:
            target_station = max(st_counts, key=st_counts.get)

    # Station d'ACHAT = la ou est l'ordre (prioritaire), sinon station cible de vente
    buy_station = (buy_cfg if (buy_cfg and buy_cfg in available_stations) else
                   (order_station_id if (order_station_id and order_station_id in available_stations) else target_station))

    # Filtrer les SELL sur la station cible de vente; les BUY sur la station d'achat
    sell_rows = [r for r in rows if r.get("station_id") == target_station] if target_station else rows
    buy_rows = [r for r in rows if r.get("station_id") == buy_station] if buy_station else rows

    buys = [r for r in buy_rows if r["side"] == 0]
    sells = [r for r in sell_rows if r["side"] == 1]

    # Fallback : si pas de BUY/SELL a la station voulue, on elargit au livre entier
    if not buys or not sells:
        buys = [r for r in rows if r["side"] == 0]
        sells = [r for r in rows if r["side"] == 1]
        if not buys or not sells:
            side_present = "BUY" if buys else ("SELL" if sells else "aucun")
            return {"ok": False, "reason": f"seul le cote {side_present} est present dans ce livre",
                    "has_buy": bool(buys), "has_sell": bool(sells),
                    "buy_station_id": buy_station, "sell_station_id": target_station}
        best_buy = max(buys, key=lambda r: r["price"])
        best_sell = min(sells, key=lambda r: r["price"])
        st_buy = best_buy["station_id"]
        st_sell = best_sell["station_id"]
    else:
        best_buy = max(buys, key=lambda r: r["price"])
        best_sell = min(sells, key=lambda r: r["price"])
        st_buy = buy_station
        st_sell = target_station

    buy_price = best_buy["price"]
    sell_price = best_sell["price"]
    vol_tradable = min(best_buy["vol"], best_sell["vol"])
    if vol_tradable <= 0:
        vol_tradable = 1

    # Frais applies sur CHAQUE station (broker differ selon NPC vs Upwell)
    broker_rate_buy = rate_for_station(st_buy, cfg)
    broker_rate_sell = rate_for_station(st_sell, cfg)
    tax_rate = sales_tax_rate(cfg["accounting"])

    # Le volume disponible (vol_tradable) est une contrainte physique stricte du carnet d'ordres.
    # Les frais et planchers CCP (100 ISK min) s'appliquent sur la valeur exacte du volume tradable réel.
    vol_dec = Decimal(str(vol_tradable))
    total_buy_val = buy_price * vol_dec
    total_sell_val = sell_price * vol_dec

    total_bf_achat = max(MINIMUM_BROKER_CHARGE_ISK, total_buy_val * broker_rate_buy)
    total_bf_vente = max(MINIMUM_BROKER_CHARGE_ISK, total_sell_val * broker_rate_sell)
    total_st_vente = total_sell_val * tax_rate

    total_frais = total_bf_achat + total_bf_vente + total_st_vente
    total_marge_brute = (sell_price - buy_price) * vol_dec
    total_marge_nette = total_marge_brute - total_frais

    # Dérivation stricte par unité sur le volume tradable réel
    bf_achat_unit = total_bf_achat / vol_dec
    bf_vente_unit = total_bf_vente / vol_dec
    st_vente_unit = total_st_vente / vol_dec
    frais_unit = total_frais / vol_dec

    marge_brute_unit = sell_price - buy_price
    marge_nette_unit = total_marge_nette / vol_dec

    pct = (total_marge_nette / total_buy_val * Decimal("100")) if total_buy_val > 0 else Decimal("0")

    from math import ceil
    net_unit_asymptotic = (sell_price - buy_price) - (buy_price * broker_rate_buy + sell_price * broker_rate_sell + sell_price * tax_rate)
    structurally_profitable = net_unit_asymptotic > Decimal("0")

    floor_free_volume = int(max(
        ceil(Decimal("100") / (buy_price * broker_rate_buy)) if buy_price * broker_rate_buy > 0 else Decimal("1"),
        ceil(Decimal("100") / (sell_price * broker_rate_sell)) if sell_price * broker_rate_sell > 0 else Decimal("1")
    ))

    breakeven_volume = None
    if structurally_profitable:
        def net_at_v(v_int):
            v_d = Decimal(str(v_int))
            b_val = buy_price * v_d
            s_val = sell_price * v_d
            b_fee = max(Decimal("100"), b_val * broker_rate_buy)
            s_fee = max(Decimal("100"), s_val * broker_rate_sell)
            s_tax = s_val * tax_rate
            return (s_val - b_val) - (b_fee + s_fee + s_tax)

        if net_at_v(1) > Decimal("0"):
            breakeven_volume = 1
        else:
            lo, hi = 1, 1_000_000_000_000
            res_v = None
            while lo <= hi:
                mid = (lo + hi) // 2
                if net_at_v(mid) > Decimal("0"):
                    res_v = mid
                    hi = mid - 1
                else:
                    lo = mid + 1
            breakeven_volume = res_v

    depth_buy = sum(int(Decimal(str(r["price"])) * Decimal(str(r["vol"])) * 100) for r in rows if r.get("side") == 0)
    depth_sell = sum(int(Decimal(str(r["price"])) * Decimal(str(r["vol"])) * 100) for r in rows if r.get("side") == 1)

    def to_c(v):
        return int((v * 100).to_integral_value(rounding="ROUND_HALF_UP"))

    kind_buy, fac_buy, corp_buy = station_kind(st_buy)
    kind_sell, fac_sell, corp_sell = station_kind(st_sell)
    kind = kind_sell  # le label principal reflete la station de vente
    return {
        "ok": True,
        "buy_price_cents": to_c(buy_price),
        "sell_price_cents": to_c(sell_price),
        "vol_tradable": vol_tradable,
        "buy_station_id": st_buy,
        "sell_station_id": st_sell,
        "station_id": st_sell,
        "station_kind": kind,
        "broker_rate_buy_pct": float(broker_rate_buy * 100),
        "broker_rate_sell_pct": float(broker_rate_sell * 100),
        "broker_rate_pct": float(broker_rate_sell * 100),
        "sales_tax_pct": float(tax_rate * 100),
        "broker_fee_buy_cents": to_c(bf_achat_unit),
        "sales_tax_cents": to_c(st_vente_unit),
        "broker_fee_sell_cents": to_c(bf_vente_unit),
        "fees_unit_cents": to_c(frais_unit),
        "margin_gross_unit_cents": to_c(marge_brute_unit),
        "margin_net_unit_cents": to_c(marge_nette_unit),
        "margin_net_total_cents": to_c(total_marge_nette),
        "margin_pct": float(pct),
        "station_label": (f"{fac_sell}/{corp_sell}" if kind == "npc" else "Upwell/citadelle"),
        "net_unit_asymptotic_cents": to_c(net_unit_asymptotic),
        "structurally_profitable": structurally_profitable,
        "breakeven_volume": breakeven_volume,
        "floor_free_volume": floor_free_volume,
        "depth_buy": depth_buy,
        "depth_sell": depth_sell,
    }


def fetch_esi_config():
    """Auto-remplit la config (standings/skills) depuis l'ESI pour le 1er perso
    connecte SSO. Scope requis: esi-characters.read_standings.v1 +
    esi-skills.read_skills.v1. Retourne le dict config ou leve une exception."""
    import urllib.request, urllib.error, json as _json
    import mmd_sso as sso
    chars = sso.connected_chars()
    if not chars:
        raise RuntimeError("aucun perso connecte SSO — connecte-toi d'abord")
    cid = chars[0]["id"]
    at = sso._access_token(cid)
    if not at:
        raise RuntimeError("token SSO indisponible (reconnecte-toi)")
    hdr = {"Authorization": f"Bearer {at}"}

    def _get(url):
        req = urllib.request.Request(url, headers=hdr)
        with urllib.request.urlopen(req, timeout=20) as r:
            return _json.loads(r.read().decode("utf-8"))

    # standings (v2)
    st = _get(f"https://esi.evetech.net/v2/characters/{cid}/standings/")
    # skills (v3)
    sk = _get(f"https://esi.evetech.net/v3/characters/{cid}/skills/")

    # skills cibles
    SKILL = {344: "broker_relations", 345: "advanced_broker_relations", 166: "accounting"}
    levels = {}
    for s in sk.get("skills", []):
        if s.get("skill_id") in SKILL:
            levels[SKILL[s["skill_id"]]] = int(s.get("active_skill_level", 0))

    # standings : deduire la faction/corp depuis la station BUY/SELL selectionnee
    # (et non plus Caldari en dur). La faction d'une station NPC se determine
    # via son systeme -> constellation -> factionID (SDE).
    try:
        import mmd_stations as stt
        sel = cfg.get("sell_station") or cfg.get("buy_station") or 0
        fac_id = stt.faction_for_station(sel) if sel else None
    except Exception:
        fac_id = None
    fac_ids = [fac_id] if fac_id else [500001]  # 500001 = Caldari State (defaut historique)
    corp_ids = []  # corporations proprietaires des stations selectionnees

    standing_map = {}
    for e in st:
        fid = int(e.get("from_id", 0))
        if fid in fac_ids or fid in corp_ids:
            # nom lisible de la faction/corp pour le mapping standings
            label = _label_for_id(fid)
            if label:
                standing_map[label] = float(e.get("standing", 0.0))

    cfg = load_config()
    cfg["broker_relations"] = levels.get("broker_relations", cfg.get("broker_relations", 0))
    cfg["advanced_broker_relations"] = levels.get("advanced_broker_relations", cfg.get("advanced_broker_relations", 0))
    cfg["accounting"] = levels.get("accounting", cfg.get("accounting", 0))
    # standings dynamiques (faction/corp deduit de la station selectionnee)
    if standing_map:
        cfg["standings"] = dict(cfg.get("standings", {}))
        cfg["standings"].update(standing_map)
    # --- persiste dans SQLite (source de verite), standings BRUTS uniquement ---
    # Echec DB -> ignore (le JSON broker_config reste la fallback), ne casse pas
    # le retour de la config.
    try:
        import repositories.character_repository as _cr
        _cr.save_trade_profile(
            cid,
            broker_relations=levels.get("broker_relations", 0),
            adv_broker=levels.get("advanced_broker_relations", 0),
            accounting=levels.get("accounting", 0),
            faction_standing_raw=standing_map.get(next(iter(standing_map), ""), 0.0),
            corp_standing_raw=0.0,
            faction_id=fac_id if fac_id else 500001,
            npc_corp_id=0,
        )
    except Exception:
        pass
    return save_config(cfg)


if __name__ == "__main__":
    # test rapide des formules
    assert npc_broker_rate(5, Decimal("5"), Decimal("8")) == Decimal("0.0119")
    assert npc_broker_rate(0, Decimal("0"), Decimal("0")) == Decimal("0.03")
    assert sales_tax_rate(5) == Decimal("0.03375")
    assert relist_discount(5) == Decimal("0.80")
    print("mmd_margin OK")
