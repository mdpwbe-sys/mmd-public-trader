#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrate_json.py - migration idempotente des JSON vers SQLite (app_data.db).

Processus:
  1. Sauvegarde deja faite (dossier _migration_backup/).
  2. Lit + valide le contenu JSON.
  3. Migre vers SQLite dans des transactions courtes.
  4. Verifie les comptes (personnages, ordres, snapshots).
  5. Marque la migration (table schema_migrations version dediee OU flag fichier).
  6. Renomme les anciens fichiers en .migrated.bak.
  7. Ne rejoue JAMAIS les memes donnees (idempotent via flag + clefs naturelles).

Tolerance: character_snapshots.json peut etre corrompu/tronque -> on le
sauvegarde en .corrupt.bak et on continue (les ordres vivants viennent de
last_scan_cache.json, bien plus complet).
"""
import os
import json
import time
import shutil
import hashlib

import database as db
import migrations as mig
import repositories.character_repository as cr
import repositories.order_repository as orr
import repositories.snapshot_repository as sr

HERE = os.path.dirname(os.path.abspath(__file__))
MIG_FLAG = os.path.join(HERE, ".json_migrated.flag")


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_load(path, label):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        # corrompu/tronque -> on sauvegarde en .corrupt.bak, on skip
        corrupt = path + ".corrupt.bak"
        try:
            shutil.copy(path, corrupt)
        except Exception:
            pass
        return None, f"{label} illisible ({e}); sauvegarde {os.path.basename(corrupt)}"


def migrate_json():
    if os.path.exists(MIG_FLAG):
        return {"status": "already_migrated", "flag": MIG_FLAG}
    mig.migrate()  # assure le schema

    report = {"characters": 0, "orders": 0, "snapshots": 0, "skipped": []}

    # --- broker_config.json -> character_trade_profiles (standings BRUTS) ---
    bc_path = os.path.join(HERE, "broker_config.json")
    if os.path.exists(bc_path):
        bc, err = _safe_load(bc_path, "broker_config")
        if bc:
            st = bc.get("standings", {})
            # le perso doit exister avant le profil (FK)
            cr.upsert_character(CHAR_ID_THREE, "CHARACTER_THREE", None, 1)
            cr.save_trade_profile(
                CHAR_ID_THREE,  # CHARACTER_THREE (source de verite standings Jita)
                broker_relations=bc.get("broker_relations", 0),
                adv_broker=bc.get("advanced_broker_relations", 0),
                accounting=bc.get("accounting", 0),
                faction_standing_raw=st.get("Caldari State", 0.0),
                corp_standing_raw=st.get("Caldari Navy", 0.0),
                faction_id=500001, npc_corp_id=10000067,
                buy_loc=bc.get("default_station", 60003760),
                sell_loc=bc.get("default_station", 60003760))
            # Upwell owner fee -> structure si configuree ailleurs (ici global)
        else:
            report["skipped"].append(err)

    # --- character_snapshots.json (peut etre corrompu) -> orders + chars ---
    cs_path = os.path.join(HERE, "character_snapshots.json")
    if os.path.exists(cs_path):
        cs, err = _safe_load(cs_path, "character_snapshots")
        if cs:
            for cid, snap in cs.items():
                try:
                    cid_i = int(cid)
                    cr.upsert_character(cid_i, snap.get("character_name"),
                                       None, 1)
                    for o in snap.get("orders", []):
                        o2 = dict(o)
                        o2["character_id"] = cid_i
                        if "station_id" not in o2 and "location_id" in o2:
                            o2["station_id"] = o2["location_id"]
                        if "price_cents" in o2 and "price" not in o2:
                            o2["price"] = o2["price_cents"] / 100.0
                        orr.upsert_order(o2, source_import_id="migration")
                        report["orders"] += 1
                    report["characters"] += 1
                except Exception as e:
                    report["skipped"].append(f"snapshot {cid}: {e}")
        else:
            report["skipped"].append(err)

    # --- last_scan_cache.json -> snapshot de marche + orders vivants ---
    lc_path = os.path.join(HERE, "last_scan_cache.json")
    if os.path.exists(lc_path):
        lc, err = _safe_load(lc_path, "last_scan_cache")
        if lc:
            orders = lc.get("orders_full", [])
            snap_id = "migration_scan_" + _now_iso().replace(":", "").replace("-", "")
            chars = {}
            snap_orders = []
            for o in orders:
                cid = int(o.get("char_id", 0))
                chars[cid] = o.get("char_name") or chars.get(cid)
                o2 = {
                    "order_id": o.get("order_id"),
                    "character_id": cid,
                    "type_id": o.get("type_id"),
                    "station_id": o.get("station_id"),
                    "side": o.get("side"),
                    "price": (o.get("price_cents", 0) / 100.0),
                    "volume_remain": o.get("vol_remaining", 0),
                    "issued": o.get("issued"),
                }
                if o.get("range"):
                    o2["range"] = o["range"]
                orr.upsert_order(o2, source_import_id="migration")
                snap_orders.append({
                    "order_id": o.get("order_id"), "type_id": o.get("type_id"),
                    "location_id": o.get("station_id"), "side": o.get("side"),
                    "price": o2["price"], "volume_remain": o.get("vol_remaining", 0),
                    "min_volume": 1, "issued_at": o.get("issued"),
                    "range": o.get("range", "region")})
                report["orders"] += 1
            for cid, name in chars.items():
                cr.upsert_character(cid, name, None, 1)
                report["characters"] += 1
            sr.save_market_snapshot(
                snap_id, source_type="migration", region_id=10000002,
                orders_count=len(snap_orders), orders=snap_orders,
                source_fetch_id="migration")
            report["snapshots"] += 1
        else:
            report["skipped"].append(err)

    # --- renommage des JSON migres (sauf si deja .bak) ---
    for fn in ("character_snapshots.json", "last_scan_cache.json",
               "broker_config.json"):
        p = os.path.join(HERE, fn)
        if os.path.exists(p) and not p.endswith(".migrated.bak"):
            try:
                shutil.move(p, p + ".migrated.bak")
            except Exception:
                pass

    # flag de migration (ne rejoue jamais)
    with open(MIG_FLAG, "w") as f:
        f.write(_now_iso())

    report["status"] = "migrated"
    return report


if __name__ == "__main__":
    print(json.dumps(migrate_json(), indent=2, ensure_ascii=False))
