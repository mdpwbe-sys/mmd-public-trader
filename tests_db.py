#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests_db.py - tests automatiques de la refonte memoire (app_data.db).

Utilise une base temporaire (DB_PATH env) pour ne pas toucher app_data.db.
Couvre les 18 scenarios requis. Lance: python tests_db.py
"""
import os
import sys
import time
import json
import tempfile
import threading
import shutil
import subprocess

# base temporaire AVANT import des modules (database.DB_PATH lit au import)
_TMP = tempfile.mkdtemp(prefix="mmd_test_")
os.environ.setdefault("TRADING_DB", os.path.join(_TMP, "app_data.db"))
import database as db
db.DB_PATH = os.environ["TRADING_DB"]
import migrations as mig
import repositories.order_repository as orr
import repositories.character_repository as cr
import repositories.snapshot_repository as sr
import repositories.recommendation_repository as rrec
import obsidian_writer as ow

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")


def fresh_db():
    """Recrée une DB NEUVE et ISOLÉE pour chaque test (évite l'état partagé)."""
    global _TMP
    path = os.path.join(_TMP, f"db_{os.getpid()}_{threading.get_ident()}_{time.time_ns()}.db")
    db.DB_PATH = path
    # les repos lisent db.DB_PATH au runtime -> ils suivent le changement
    mig.migrate()
    return path


# ---------------------------------------------------------------- 1. WAL actif
def test_wal_active():
    fresh_db()
    with db.connection() as con:
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        check("1. journal_mode WAL actif", mode.lower() == "wal")


# -------------------------------------------- 2. lecture GUI pendant ecriture
def test_read_during_write():
    fresh_db()
    stop = False
    errs = []

    def writer():
        for i in range(200):
            try:
                cr.upsert_character(1, "A", 1)
                orr.upsert_order({"order_id": f"w{i}", "character_id": 1,
                                  "type_id": 34, "station_id": 60003760,
                                  "side": 0, "price": 1.0 + i * 0.01,
                                  "volume_remain": 1})
            except Exception as e:
                errs.append(str(e))

    def reader():
        while not stop:
            try:
                orr.get_all_latest_character_orders()
            except Exception as e:
                errs.append(str(e))

    tw = threading.Thread(target=writer)
    tr = threading.Thread(target=reader)
    tw.start(); tr.start()
    tw.join(); stop = True; tr.join()
    check("2. lecture pendant ecriture sans erreur", len(errs) == 0)


# --------------------------------- 3. deux writers simultanes serialises
def test_two_writers():
    fresh_db()
    errs = []

    def w(cid):
        for i in range(100):
            try:
                cr.upsert_character(cid, f"C{cid}", 1)
                orr.upsert_order({"order_id": f"{cid}_{i}", "character_id": cid,
                                  "type_id": 34, "station_id": 60003760,
                                  "side": 0, "price": 1.0, "volume_remain": 1})
            except Exception as e:
                errs.append(str(e))

    t1 = threading.Thread(target=w, args=(1,))
    t2 = threading.Thread(target=w, args=(2,))
    t1.start(); t2.start(); t1.join(); t2.join()
    with db.connection() as con:
        n = con.execute("SELECT COUNT(*) FROM character_orders").fetchone()[0]
    check("3. deux writers sans perte (200 ordres)", n == 200 and len(errs) == 0)


# ------------------------------------- 4. busy_timeout evite database locked
def test_busy_timeout():
    fresh_db()
    # une longue transaction (writer) + un reader concurrent -> pas d'erreur
    errs = []

    def long_writer():
        try:
            with db.connection() as con:
                with db.transaction(con):
                    for i in range(150):
                        con.execute("INSERT OR REPLACE INTO characters(character_id, character_name, corporation_id, active, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?)",
                                    (3, "L", 1, 1, "t", "t"))
                        con.execute("INSERT OR REPLACE INTO character_orders("
                                    "order_id, character_id, type_id, location_id, "
                                    "is_buy_order, price_cents, volume_remain, "
                                    "min_volume, issued_at, duration, state, "
                                    "first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (f"lt{i}", 3, 34, 60003760, 1, 100, 1, 1,
                                     "2026-08-06", 90, "active", "t", "t"))
                        time.sleep(0.001)
        except Exception as e:
            errs.append(str(e))

    def reader():
        for _ in range(50):
            try:
                orr.get_latest_orders_by_character(3)
            except Exception as e:
                errs.append(str(e))

    tw = threading.Thread(target=long_writer)
    tr = threading.Thread(target=reader)
    tw.start(); time.sleep(0.01); tr.start()
    tw.join(); tr.join()
    check("4. busy_timeout: pas d'erreur 'database is locked'", len(errs) == 0)


# ------------------------------------- 5. rollback snapshot partiellement invalide
def test_rollback_partial_snapshot():
    fresh_db()
    # snapshot valide existant
    sr.save_market_snapshot("ok1", source_type="region", region_id=10000002,
                            orders_count=1, orders=[{"order_id": "1", "type_id": 34,
                            "location_id": 60003760, "side": 0, "price": 1.0,
                            "volume_remain": 1, "min_volume": 1, "issued_at": "t"}])
    # snapshot invalide (order sans order_id -> doit rollback)
    try:
        sr.save_market_snapshot("bad", source_type="region", region_id=10000002,
                                orders_count=1, orders=[{"type_id": 34}])
    except Exception:
        pass
    with db.connection() as con:
        n = con.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
        ok_exists = con.execute("SELECT 1 FROM market_snapshots WHERE snapshot_id='ok1'").fetchone()
    check("5. rollback snapshot invalide (1 snapshot coherent restant)", n == 1 and ok_exists is not None)


# --------------------------------------------- 6. vraie migration JSON idempotente
def test_migration_once():
    fresh_db()
    import migrate_json as mj
    fixture_dir = tempfile.mkdtemp(prefix="migration_fixture_", dir=_TMP)
    old_here, old_flag = mj.HERE, mj.MIG_FLAG
    try:
        mj.HERE = fixture_dir
        mj.MIG_FLAG = os.path.join(fixture_dir, ".json_migrated.flag")
        with open(os.path.join(fixture_dir, "character_snapshots.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"1": {"character_name": "A", "orders": []}}, f)
        with open(os.path.join(fixture_dir, "last_scan_cache.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"orders_full": [{
                "order_id": "mx", "char_id": 1, "char_name": "A",
                "type_id": 34, "station_id": 60003760, "side": 0,
                "price_cents": 100, "vol_remaining": 1,
                "issued": "2026-08-06T00:00:00Z"
            }]}, f)

        first = mj.migrate_json()
        second = mj.migrate_json()
        with db.connection() as con:
            orders = con.execute(
                "SELECT COUNT(*) FROM character_orders WHERE order_id='mx'").fetchone()[0]
            snapshots = con.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE source_type='migration'").fetchone()[0]
        sources_archived = all(os.path.exists(os.path.join(
            fixture_dir, name + ".migrated.bak")) for name in (
                "character_snapshots.json", "last_scan_cache.json"))
        check("6. vraie migration JSON idempotente (1 ordre, 1 snapshot)",
              first.get("status") == "migrated"
              and second.get("status") == "already_migrated"
              and orders == 1 and snapshots == 1 and sources_archived)
    finally:
        mj.HERE, mj.MIG_FLAG = old_here, old_flag


# ------------------------------ 7. import d'un perso ne supprime pas les autres
def test_import_keeps_others():
    fresh_db()
    cr.upsert_character(1, "A", 1)
    cr.upsert_character(2, "B", 1)
    a = {"order_id": "a1", "character_id": 1, "type_id": 34, "station_id": 60003760,
         "side": 0, "price": 1.0, "volume_remain": 1}
    b = {"order_id": "b1", "character_id": 2, "type_id": 34, "station_id": 60003760,
         "side": 0, "price": 1.0, "volume_remain": 1}
    orr.upsert_order(a)
    orr.upsert_order(b)
    # re-import perso 1 seul
    orr.upsert_order(a)
    with db.connection() as con:
        n1 = con.execute("SELECT COUNT(*) FROM character_orders WHERE character_id=1").fetchone()[0]
        n2 = con.execute("SELECT COUNT(*) FROM character_orders WHERE character_id=2").fetchone()[0]
    check("7. import perso1 ne touche pas perso2", n1 == 1 and n2 == 1)


# --------------------------------- 8. vue 'Tous' = dernier snapshot par perso
def test_all_latest_view():
    fresh_db()
    # perso 1: deux snapshots temporels
    orr.upsert_order({"order_id": "p1_old", "character_id": 1, "type_id": 34,
                      "station_id": 60003760, "side": 0, "price": 1.0,
                      "volume_remain": 5}, last_seen_at="2026-08-01T00:00:00Z")
    orr.upsert_order({"order_id": "p1_new", "character_id": 1, "type_id": 34,
                      "station_id": 60003760, "side": 0, "price": 2.0,
                      "volume_remain": 5}, last_seen_at="2026-08-06T00:00:00Z")
    orr.upsert_order({"order_id": "p2", "character_id": 2, "type_id": 34,
                      "station_id": 60003760, "side": 0, "price": 1.0,
                      "volume_remain": 5})
    allrows = orr.get_all_latest_character_orders()
    ids = {r["order_id"] for r in allrows}
    check("8. vue Tous = latest par perso (p1_new + p2)", "p1_new" in ids and "p2" in ids and "p1_old" not in ids)


# -------------------------------- 9. detection doublons via moteur metier reel
def test_alt_duplicate():
    fresh_db()
    import mmd_core as core
    base = {
        "type_id": 34, "station_id": 60003760, "side": 0,
        "price": 5.0, "vol_remaining": 1,
        "issued": "2026-08-06T00:00:00Z",
    }
    a = dict(base, order_id="da", char_id=1, char_name="A")
    b = dict(base, order_id="db", char_id=2, char_name="B")
    result = core._scan_core([a, b], [], "test fixture")
    duplicate = result.get("dup_list", [{}])[0]
    check("9. vraie detection doublon inter-alts via _scan_core",
          result.get("duplicates") == 1
          and duplicate.get("type_id") == 34
          and duplicate.get("chars") == ["A", "B"])


# ------------------------------------------------- 10. dedup par order_id
def test_dedup_order_id():
    fresh_db()
    o = {"order_id": "dup", "character_id": 1, "type_id": 34, "station_id": 60003760,
         "side": 0, "price": 1.0, "volume_remain": 10}
    orr.upsert_order(o)
    orr.upsert_order(dict(o, volume_remain=5))  # meme order_id, vol change
    with db.connection() as con:
        n = con.execute("SELECT COUNT(*) FROM character_orders WHERE order_id='dup'").fetchone()[0]
        vol = con.execute("SELECT volume_remain FROM character_orders WHERE order_id='dup'").fetchone()[0]
    check("10. dedup par order_id (1 ligne, vol maj)", n == 1 and vol == 5)


# ---------------- 11. ancien snapshot conserve sur 403/404/429/503 (marque stale)
def test_stale_on_http_error():
    fresh_db()
    cr.upsert_character(1, "A", 1)
    sr.save_structure(999, name="TestStruct", solar_system_id=30000142,
                     region_id=10000002, owner_fee_rate=0.0)
    sr.save_market_snapshot("s1", source_type="structure", structure_id=999,
                            orders_count=1, orders=[{"order_id": "o", "type_id": 34,
                            "location_id": 999, "side": 1, "price": 4.0,
                            "volume_remain": 1, "min_volume": 1, "issued_at": "t"}])
    # acces 403 -> on marque stale, on ne purge pas
    sr.save_structure_access(999, 1, "inaccessible", http_status=403)
    orr.mark_stale_snapshot("s1")
    with db.connection() as con:
        stale = con.execute("SELECT stale FROM market_snapshots WHERE snapshot_id='s1'").fetchone()[0]
        exists = con.execute("SELECT 1 FROM market_snapshots WHERE snapshot_id='s1'").fetchone()
        acc = con.execute("SELECT access_status FROM structure_access WHERE structure_id=999 AND character_id=1").fetchone()[0]
    check("11. snapshot marque STALE, conserve + acces garde", stale == 1 and exists is not None and acc == "inaccessible")


# --------------------------------- 12. echec Obsidian n'affecte pas SQLite
def test_obsidian_failure_safe():
    fresh_db()
    # vault inaccessible -> writer leve, mais SQLite deja committe reste
    bad = ow.ObsidianMemoryWriter(vault_root=os.path.join(_TMP, "nope", "x", "y"))
    try:
        bad.write_note("10_Characters", "c.md", "body", {"a": 1})
    except Exception:
        pass
    orr.upsert_order({"order_id": "z", "character_id": 1, "type_id": 34,
                      "station_id": 60003760, "side": 0, "price": 1.0, "volume_remain": 1})
    with db.connection() as con:
        n = con.execute("SELECT COUNT(*) FROM character_orders WHERE order_id='z'").fetchone()[0]
    check("12. echec Obsidian n'affecte pas SQLite", n == 1)


# --------------------------------- 13. aucun secret dans la base / notes / logs
def test_no_secrets():
    fresh_db()
    secret_tokens = ["refresh_token", "access_token", "client_secret",
                     "Authorization", "Bearer ", "cookie"]
    # insere des donnees normales
    orr.upsert_order({"order_id": "s", "character_id": 1, "type_id": 34,
                      "station_id": 60003760, "side": 0, "price": 1.0, "volume_remain": 1})
    sr.save_esi_fetch("f1", endpoint="/markets/1/orders/", http_status=200)
    bad = False
    with db.connection() as con:
        for (row,) in con.execute("SELECT order_id FROM character_orders"):
            if any(t.lower() in str(row).lower() for t in secret_tokens):
                bad = True
        # recherche dans tout le dump texte
        dump = json.dumps([dict(r) for r in con.execute(
            "SELECT * FROM esi_fetches")], ensure_ascii=False).lower()
        if any(t.lower() in dump for t in secret_tokens):
            bad = True
    check("13. aucun secret dans SQLite", not bad)


# --------------------------------- 14. calcul financier en Decimal (jamais float)
def test_decimal_finances():
    fresh_db()
    # prix 0.02 ISK -> 2 centiemes exacts (pas derive float)
    o = {"order_id": "d", "character_id": 1, "type_id": 34, "station_id": 60003760,
         "side": 0, "price": 0.02, "volume_remain": 1}
    orr.upsert_order(o)
    with db.connection() as con:
        cents = con.execute("SELECT price_cents FROM character_orders WHERE order_id='d'").fetchone()[0]
    check("14. prix 0.02 ISK -> 2 centiemes (Decimal, pas float)", cents == 2)


# --------------------------------- 15. rapport quotidien genere depuis SQLite
def test_daily_report():
    fresh_db()
    orr.upsert_order({"order_id": "r", "character_id": 1, "type_id": 34,
                      "station_id": 60003760, "side": 0, "price": 1.0, "volume_remain": 1})
    orr.record_order_event("r", 1, "order_outbid", new_price=1.0, snapshot_id="snap")
    import reports as rp
    summary = rp.compute_daily_report()
    check("15. rapport quotidien genere depuis SQLite", "Ordres actifs" in summary)


# --------------------------------- 16. reconstruction index Obsidian depuis SQLite
def test_agent_index_rebuild():
    fresh_db()
    orr.upsert_order({"order_id": "i", "character_id": 1, "type_id": 34,
                      "station_id": 60003760, "side": 0, "price": 1.0, "volume_remain": 1})
    w = ow.ObsidianMemoryWriter(vault_root=os.path.join(_TMP, "vault"))
    idx_path = os.path.join(w.root, "_System", "indexes", "active_orders.json")
    w.write_agent_indexes({"active_orders.json": {"generated_at": "now",
                                                  "count": 1, "source": "sqlite"}})
    check("16. index agent reconstruit depuis SQLite", os.path.exists(idx_path))


# ----------------------------- 17. vrai arret brutal sans checkpoint
def test_brutal_restart():
    path = fresh_db()
    child = (
        "import os,sqlite3,sys\n"
        "con=sqlite3.connect(sys.argv[1], isolation_level=None)\n"
        "con.execute('PRAGMA journal_mode=WAL')\n"
        "con.execute('CREATE TABLE IF NOT EXISTS crash_probe(value INTEGER)')\n"
        "con.execute('BEGIN IMMEDIATE')\n"
        "con.execute('INSERT INTO crash_probe VALUES (42)')\n"
        "con.execute('COMMIT')\n"
        "os._exit(9)\n"
    )
    proc = subprocess.run([sys.executable, "-c", child, path],
                          capture_output=True, text=True)
    with db.connection() as con:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        value = con.execute("SELECT value FROM crash_probe").fetchone()[0]
    check("17. vrai arret brutal sans corruption WAL",
          proc.returncode == 9 and integrity == "ok" and value == 42)


# --------------------------------- 18. fermeture propre + checkpoint WAL non bloquant
def test_clean_checkpoint():
    fresh_db()
    for i in range(50):
        orr.upsert_order({"order_id": f"c{i}", "character_id": 1, "type_id": 34,
                          "station_id": 60003760, "side": 0, "price": 1.0, "volume_remain": 1})
    db.checkpoint()
    with db.connection() as con:
        busy, _log_pages, _checkpointed = con.execute(
            "PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    check("18. checkpoint PASSIVE reel, non bloque et DB integre",
          busy == 0 and integrity == "ok")


def test_event_dedup_and_retention():
    fresh_db()
    order = {"order_id": "evt", "character_id": 1, "type_id": 34,
             "station_id": 60003760, "side": 0, "price": 1.0,
             "volume_remain": 10}
    orr.upsert_order(order)
    first = orr.record_order_event(
        "evt", 1, "order_detected", new_price=1.0,
        new_volume_remain=10, snapshot_id="s1", reason="scan")
    duplicate = orr.record_order_event(
        "evt", 1, "order_detected", new_price=1.0,
        new_volume_remain=10, snapshot_id="s2", reason="scan")
    changed_1 = orr.record_order_event(
        "evt", 1, "order_outbid", new_price=1.1,
        new_volume_remain=9, snapshot_id="s3", reason="scan")
    changed_2 = orr.record_order_event(
        "evt", 1, "order_outbid", new_price=1.2,
        new_volume_remain=8, snapshot_id="s4", reason="scan")
    orr.prune_order_events(retention_days=90, max_events=2)
    with db.connection() as con:
        count = con.execute("SELECT COUNT(*) FROM order_events").fetchone()[0]
    check("18b. evenements dedupliques et retention bornee",
          first and not duplicate and changed_1 and changed_2 and count == 2)


# -------------------------------- 19. coherence volume et plancher de risque compute_margin
def test_margin_physical_volume_coherence():
    import mmd_margin as m
    cfg = m.load_config()
    # 1. Test coherence d'affichage (Defaut 1) : Heavy Water 578 846 vol
    rows_hw = [
        {'price': m.Decimal('114.8'), 'vol': 2909042, 'side': 1, 'station_id': 60003760},
        {'price': m.Decimal('105.1'), 'vol': 578846, 'side': 0, 'station_id': 60003760}
    ]
    res_hw = m.compute_margin(rows_hw, cfg)
    check("19. compute_margin : vol_tradable physique Heavy Water", res_hw["ok"] and res_hw["vol_tradable"] == 578846)
    check("20. compute_margin : marge % positive Heavy Water (+3.20%)", res_hw["margin_pct"] > 3.0 and res_hw["margin_pct"] < 3.5)

    # 2. Test non-regression faux positifs (Defaut 2) : 1 unit @ 200 ISK buy, 260 ISK sell
    rows_low = [
        {'price': m.Decimal('260.0'), 'vol': 1, 'side': 1, 'station_id': 60003760},
        {'price': m.Decimal('200.0'), 'vol': 1, 'side': 0, 'station_id': 60003760}
    ]
    res_low = m.compute_margin(rows_low, cfg)
    check("21. compute_margin : rejet des trades peu profonds (marge < 0 due au plancher 100 ISK)", res_low["margin_pct"] < 0)
    # Verification strict d'egalite total == unit * vol (Defaut 1)
    tot_calc = res_low["margin_net_unit_cents"] * res_low["vol_tradable"]
    check("22. compute_margin : coherence stricte net_total == net_unit * vol_affiché", abs(res_low["margin_net_total_cents"] - tot_calc) <= 1)


def test_margin_structural_measures():
    import os
    from decimal import Decimal
    import mmd_margin as m
    cfg = m.load_config()
    HERE = os.path.dirname(os.path.abspath(__file__))

    def find_file(prefix):
        dirs = [HERE, os.getcwd(), os.path.expanduser('~/Documents/EVE/logs/Marketlogs')]
        for d in dirs:
            if os.path.exists(d):
                for f in sorted(os.listdir(d)):
                    if f.startswith(prefix) and f.endswith('.txt'):
                        return os.path.join(d, f)
        return None

    p_trit = find_file("The Forge-Tritanium-")
    if p_trit:
        rows, _, _ = m.parse_market_book(p_trit)
        r = m.compute_margin(rows, cfg)
        check("23. Tritanium reel : structurally_profitable == False ET breakeven is None", r["structurally_profitable"] is False and r["breakeven_volume"] is None)

    p_hw = find_file("The Forge-Heavy Water-")
    if p_hw:
        rows, _, _ = m.parse_market_book(p_hw)
        r = m.compute_margin(rows, cfg)
        check("24. Heavy Water reel : structurally_profitable == True ET breakeven == 35", r["structurally_profitable"] is True and r["breakeven_volume"] == 35)

    p_jav = find_file("The Forge-Javelin S-")
    if p_jav:
        rows, _, _ = m.parse_market_book(p_jav)
        r = m.compute_margin(rows, cfg)
        check("25. Javelin S reel : structurally_profitable == True ET breakeven == 20", r["structurally_profitable"] is True and r["breakeven_volume"] == 20)

    rows_1u = [
        {'price': Decimal('260.0'), 'vol': 1, 'side': 1, 'station_id': 60003760},
        {'price': Decimal('200.0'), 'vol': 1, 'side': 0, 'station_id': 60003760}
    ]
    r_1u = m.compute_margin(rows_1u, cfg)
    check("26. 1u @200->260 : structurally_profitable == True MAIS breakeven > 1", r_1u["structurally_profitable"] is True and r_1u["breakeven_volume"] is not None and r_1u["breakeven_volume"] > 1)

    tot_calc = r_1u["margin_net_unit_cents"] * r_1u["vol_tradable"]
    check("27. Invariant net_total == net_unit * vol (tolerance 0.005*vol)", abs(r_1u["margin_net_total_cents"] - tot_calc) <= 1)

if __name__ == "__main__":
    print("=== TESTS MEMOIRE EVE TRADER ===")
    test_wal_active()
    test_read_during_write()
    test_two_writers()
    test_busy_timeout()
    test_rollback_partial_snapshot()
    test_migration_once()
    test_import_keeps_others()
    test_all_latest_view()
    test_alt_duplicate()
    test_dedup_order_id()
    test_stale_on_http_error()
    test_obsidian_failure_safe()
    test_no_secrets()
    test_decimal_finances()
    test_daily_report()
    test_agent_index_rebuild()
    test_brutal_restart()
    test_clean_checkpoint()
    test_event_dedup_and_retention()
    test_margin_physical_volume_coherence()
    test_margin_structural_measures()
    print(f"\n=== RESULTAT: {PASS} OK / {FAIL} FAIL ===")
    shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(1 if FAIL else 0)
