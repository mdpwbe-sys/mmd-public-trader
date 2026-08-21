#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrations.py - migrations versionnees du schema app_data.db.

Convention SQL choisie (documentee et deterministe) :
  - PRIX / TAUX / TAXES / FEES / PROFITS : stockes en CENTIEMES d'ISK sous forme
    INTEGER (ex: 28.01 ISK -> 2801). Jamais de float. Decimal uniquement cote
    code metier (mmd_price.to_cents / from_cents).
  - TIMESTAMPS : texte ISO 8601 UTC (suffixe 'Z'), ex '2026-08-06T20:00:00Z'.
    Conversion heure locale uniquement dans l'interface.
  - RANGE : texte ESI explicite ('station','solar_system','region',
    'constellation','region_boundary_1'..'5') OU entier CCP (0..5) conserve tel
    quel en TEXT pour rester source de verite.

Migrations idempotentes : chaque version appliquee est enregistree dans
schema_migrations et ne rejoue jamais.
"""

import os
import time
import database as db

# Liste ordonnee des migrations. Chaque item: (version, description, sql_statements)
MIGRATIONS = [
    (1, "schema initial: characters, orders, events, snapshots, esi, structures, reco",
     """
     CREATE TABLE IF NOT EXISTS schema_migrations (
         version INTEGER PRIMARY KEY,
         applied_at TEXT NOT NULL,
         description TEXT
     );

     CREATE TABLE IF NOT EXISTS characters (
         character_id INTEGER PRIMARY KEY,
         character_name TEXT,
         corporation_id INTEGER,
         active INTEGER NOT NULL DEFAULT 1,
         first_seen_at TEXT,
         last_seen_at TEXT
     );

     CREATE TABLE IF NOT EXISTS character_trade_profiles (
         character_id INTEGER PRIMARY KEY,
         broker_relations_level INTEGER NOT NULL DEFAULT 0,
         advanced_broker_relations_level INTEGER NOT NULL DEFAULT 0,
         accounting_level INTEGER NOT NULL DEFAULT 0,
         faction_standing_raw REAL NOT NULL DEFAULT 0.0,
         corporation_standing_raw REAL NOT NULL DEFAULT 0.0,
         faction_id INTEGER,
         npc_corporation_id INTEGER,
         preferred_buy_location_id INTEGER,
         preferred_sell_location_id INTEGER,
         updated_at TEXT,
         FOREIGN KEY (character_id) REFERENCES characters(character_id)
     );

     CREATE TABLE IF NOT EXISTS character_orders (
         order_id TEXT PRIMARY KEY,
         character_id INTEGER NOT NULL,
         type_id INTEGER NOT NULL,
         location_id INTEGER NOT NULL,
         region_id INTEGER,
         is_buy_order INTEGER NOT NULL,
         range TEXT,
         price_cents INTEGER NOT NULL,
         volume_total INTEGER NOT NULL DEFAULT 0,
         volume_remain INTEGER NOT NULL DEFAULT 0,
         min_volume INTEGER NOT NULL DEFAULT 1,
         issued_at TEXT,
         duration INTEGER,
         state TEXT NOT NULL DEFAULT 'active',
         first_seen_at TEXT,
         last_seen_at TEXT,
         source_import_id TEXT,
         source_fetch_id TEXT,
         FOREIGN KEY (character_id) REFERENCES characters(character_id)
     );

     CREATE TABLE IF NOT EXISTS order_events (
         event_id INTEGER PRIMARY KEY AUTOINCREMENT,
         order_id TEXT NOT NULL,
         character_id INTEGER,
         event_type TEXT NOT NULL,
         occurred_at TEXT NOT NULL,
         previous_price_cents INTEGER,
         new_price_cents INTEGER,
         previous_volume_remain INTEGER,
         new_volume_remain INTEGER,
         snapshot_id TEXT,
         reason TEXT,
         metadata_json TEXT,
         FOREIGN KEY (order_id) REFERENCES character_orders(order_id)
     );

     CREATE TABLE IF NOT EXISTS market_exports (
         import_id TEXT PRIMARY KEY,
         character_id INTEGER,
         source_path TEXT,
         source_filename TEXT,
         file_size INTEGER,
         modified_at TEXT,
         file_hash TEXT,
         imported_at TEXT,
         row_count INTEGER,
         status TEXT,
         error_message TEXT
     );

     CREATE TABLE IF NOT EXISTS esi_fetches (
         fetch_id TEXT PRIMARY KEY,
         endpoint TEXT,
         character_id INTEGER,
         requested_at TEXT,
         completed_at TEXT,
         http_status INTEGER,
         etag TEXT,
         expires_at TEXT,
         last_modified TEXT,
         pages_expected INTEGER,
         pages_received INTEGER,
         coherent INTEGER NOT NULL DEFAULT 1,
         rate_limit_metadata_json TEXT,
         error_message TEXT
     );

     CREATE TABLE IF NOT EXISTS market_snapshots (
         snapshot_id TEXT PRIMARY KEY,
         source_type TEXT,
         region_id INTEGER,
         structure_id INTEGER,
         type_id INTEGER,
         fetched_at TEXT,
         expires_at TEXT,
         last_modified TEXT,
         pages_count INTEGER,
         orders_count INTEGER,
         coherent INTEGER NOT NULL DEFAULT 1,
         stale INTEGER NOT NULL DEFAULT 0,
         source_fetch_id TEXT
     );

     CREATE TABLE IF NOT EXISTS market_snapshot_orders (
         snapshot_id TEXT NOT NULL,
         order_id TEXT NOT NULL,
         type_id INTEGER NOT NULL,
         location_id INTEGER NOT NULL,
         system_id INTEGER,
         region_id INTEGER,
         is_buy_order INTEGER NOT NULL,
         range TEXT,
         price_cents INTEGER NOT NULL,
         volume_remain INTEGER NOT NULL DEFAULT 0,
         min_volume INTEGER NOT NULL DEFAULT 1,
         issued_at TEXT,
         PRIMARY KEY (snapshot_id, order_id),
         FOREIGN KEY (snapshot_id) REFERENCES market_snapshots(snapshot_id)
     );

     CREATE TABLE IF NOT EXISTS structures (
         structure_id INTEGER PRIMARY KEY,
         structure_name TEXT,
         solar_system_id INTEGER,
         region_id INTEGER,
         owner_fee_rate REAL,
         last_info_success_at TEXT
     );

     CREATE TABLE IF NOT EXISTS structure_access (
         structure_id INTEGER NOT NULL,
         character_id INTEGER NOT NULL,
         access_status TEXT,
         last_checked_at TEXT,
         last_success_at TEXT,
         last_http_status INTEGER,
         PRIMARY KEY (structure_id, character_id),
         FOREIGN KEY (structure_id) REFERENCES structures(structure_id),
         FOREIGN KEY (character_id) REFERENCES characters(character_id)
     );

     CREATE TABLE IF NOT EXISTS trade_recommendations (
         recommendation_id TEXT PRIMARY KEY,
         created_at TEXT NOT NULL,
         character_id INTEGER,
         order_id TEXT,
         type_id INTEGER,
         buy_location_id INTEGER,
         sell_location_id INTEGER,
         action TEXT,
         current_price_cents INTEGER,
         recommended_price_cents INTEGER,
         estimated_broker_fee_cents INTEGER,
         estimated_relist_fee_cents INTEGER,
         estimated_sales_tax_cents INTEGER,
         estimated_profit_cents INTEGER,
         confidence TEXT,
         reason TEXT,
         snapshot_id TEXT,
         algorithm_version TEXT
     );

     CREATE TABLE IF NOT EXISTS trade_decisions (
         decision_id TEXT PRIMARY KEY,
         recommendation_id TEXT,
         decided_at TEXT,
         user_action TEXT,
         actual_price_cents INTEGER,
         notes TEXT,
         FOREIGN KEY (recommendation_id) REFERENCES trade_recommendations(recommendation_id)
     );

     CREATE TABLE IF NOT EXISTS trade_outcomes (
         outcome_id TEXT PRIMARY KEY,
         decision_id TEXT,
         evaluated_at TEXT,
         quantity_filled INTEGER,
         realized_revenue_cents INTEGER,
         realized_cost_cents INTEGER,
         realized_fees_cents INTEGER,
         realized_profit_cents INTEGER,
         result_classification TEXT,
         lesson TEXT,
         FOREIGN KEY (decision_id) REFERENCES trade_decisions(decision_id)
     );
     """),

    (2, "index operationnels sur les requetes frequentes",
     """
     CREATE INDEX IF NOT EXISTS idx_orders_character
         ON character_orders(character_id);
     CREATE INDEX IF NOT EXISTS idx_orders_type_location
         ON character_orders(type_id, location_id, is_buy_order);
     CREATE INDEX IF NOT EXISTS idx_orders_last_seen
         ON character_orders(last_seen_at);
     CREATE INDEX IF NOT EXISTS idx_snapshot_orders_lookup
         ON market_snapshot_orders(snapshot_id, type_id, location_id, is_buy_order, price_cents);
     CREATE INDEX IF NOT EXISTS idx_order_events_order_time
         ON order_events(order_id, occurred_at);
     CREATE INDEX IF NOT EXISTS idx_recommendations_character_time
         ON trade_recommendations(character_id, created_at);
     CREATE INDEX IF NOT EXISTS idx_fetch_endpoint_time
         ON esi_fetches(endpoint, requested_at);
     CREATE INDEX IF NOT EXISTS idx_exports_char_time
         ON market_exports(character_id, imported_at);
     CREATE INDEX IF NOT EXISTS idx_structures_system
         ON structures(solar_system_id);
     """),
    (3, "table historical_market_daily pour stockage long-terme > 1 an (ESI + EVE Ref)",
     """
     CREATE TABLE IF NOT EXISTS historical_market_daily (
         region_id INTEGER NOT NULL,
         type_id INTEGER NOT NULL,
         date TEXT NOT NULL,
         avg_cents INTEGER NOT NULL,
         high_cents INTEGER NOT NULL,
         low_cents INTEGER NOT NULL,
         volume INTEGER NOT NULL,
         order_count INTEGER NOT NULL,
         PRIMARY KEY (region_id, type_id, date)
     );
     CREATE INDEX IF NOT EXISTS idx_hist_daily_type
         ON historical_market_daily(region_id, type_id, date);
     """),
    (4, "portefeuille: assets, transactions FIFO, contrats et reglages scopes",
     """
     CREATE TABLE IF NOT EXISTS asset_snapshots (
         owner_kind TEXT NOT NULL CHECK(owner_kind IN ('character','corporation')),
         owner_id INTEGER NOT NULL CHECK(owner_id > 0),
         division_id INTEGER NOT NULL DEFAULT 0,
         snapshot_id TEXT NOT NULL,
         captured_at TEXT NOT NULL,
         coherent INTEGER NOT NULL DEFAULT 1 CHECK(coherent IN (0,1)),
         items_count INTEGER NOT NULL DEFAULT 0 CHECK(items_count >= 0),
         source_fetch_id TEXT,
         PRIMARY KEY (owner_kind, owner_id, division_id, snapshot_id),
         CHECK((owner_kind='character' AND division_id=0) OR
               (owner_kind='corporation' AND division_id BETWEEN 0 AND 7))
     );

     CREATE TABLE IF NOT EXISTS asset_snapshot_items (
         owner_kind TEXT NOT NULL,
         owner_id INTEGER NOT NULL,
         division_id INTEGER NOT NULL DEFAULT 0,
         snapshot_id TEXT NOT NULL,
         item_id INTEGER NOT NULL,
         type_id INTEGER NOT NULL CHECK(type_id > 0),
         quantity INTEGER NOT NULL CHECK(quantity > 0),
         location_id INTEGER NOT NULL,
         location_type TEXT,
         location_flag TEXT,
         parent_item_id INTEGER,
         root_container_id INTEGER,
         hierarchy_depth INTEGER NOT NULL DEFAULT 0 CHECK(hierarchy_depth >= 0),
         is_singleton INTEGER NOT NULL DEFAULT 0 CHECK(is_singleton IN (0,1)),
         is_blueprint_copy INTEGER NOT NULL DEFAULT 0 CHECK(is_blueprint_copy IN (0,1)),
         item_name TEXT,
         PRIMARY KEY (owner_kind, owner_id, division_id, snapshot_id, item_id),
         FOREIGN KEY (owner_kind, owner_id, division_id, snapshot_id)
             REFERENCES asset_snapshots(owner_kind, owner_id, division_id, snapshot_id)
             ON DELETE CASCADE
     );

     CREATE TABLE IF NOT EXISTS trade_transactions (
         owner_kind TEXT NOT NULL CHECK(owner_kind IN ('character','corporation')),
         owner_id INTEGER NOT NULL CHECK(owner_id > 0),
         division_id INTEGER NOT NULL,
         transaction_id INTEGER NOT NULL,
         occurred_at TEXT NOT NULL,
         type_id INTEGER NOT NULL CHECK(type_id > 0),
         location_id INTEGER NOT NULL,
         client_id INTEGER,
         journal_ref_id INTEGER,
         is_buy INTEGER NOT NULL CHECK(is_buy IN (0,1)),
         is_personal INTEGER NOT NULL DEFAULT 0 CHECK(is_personal IN (0,1)),
         quantity INTEGER NOT NULL CHECK(quantity > 0),
         unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
         broker_fee_cents INTEGER NOT NULL DEFAULT 0 CHECK(broker_fee_cents >= 0),
         sales_tax_cents INTEGER NOT NULL DEFAULT 0 CHECK(sales_tax_cents >= 0),
         fees_status TEXT NOT NULL DEFAULT 'missing'
             CHECK(fees_status IN ('missing','estimated','actual')),
         matched_quantity INTEGER NOT NULL DEFAULT 0
             CHECK(matched_quantity >= 0 AND matched_quantity <= quantity),
         realized_cost_cents INTEGER,
         realized_profit_cents INTEGER,
         fifo_status TEXT NOT NULL DEFAULT 'pending'
             CHECK(fifo_status IN ('pending','complete','partial','unknown_basis')),
         fifo_version TEXT,
         source_fetch_id TEXT,
         ingested_at TEXT NOT NULL,
         PRIMARY KEY (owner_kind, owner_id, division_id, transaction_id),
         CHECK((owner_kind='character' AND division_id=0) OR
               (owner_kind='corporation' AND division_id BETWEEN 1 AND 7))
     );

     CREATE TABLE IF NOT EXISTS contract_assets (
         owner_kind TEXT NOT NULL CHECK(owner_kind IN ('character','corporation')),
         owner_id INTEGER NOT NULL CHECK(owner_id > 0),
         division_id INTEGER NOT NULL DEFAULT 0,
         contract_id INTEGER NOT NULL,
         record_id INTEGER NOT NULL,
         type_id INTEGER NOT NULL CHECK(type_id > 0),
         quantity INTEGER NOT NULL CHECK(quantity > 0),
         raw_quantity INTEGER,
         is_included INTEGER NOT NULL DEFAULT 1 CHECK(is_included IN (0,1)),
         is_singleton INTEGER NOT NULL DEFAULT 0 CHECK(is_singleton IN (0,1)),
         is_acquisition INTEGER NOT NULL DEFAULT 0 CHECK(is_acquisition IN (0,1)),
         issuer_id INTEGER,
         acceptor_id INTEGER,
         contract_type TEXT,
         contract_status TEXT,
         issued_at TEXT,
         completed_at TEXT,
         contract_price_cents INTEGER NOT NULL DEFAULT 0 CHECK(contract_price_cents >= 0),
         allocated_cost_cents INTEGER CHECK(allocated_cost_cents >= 0),
         allocation_method TEXT,
         source_fetch_id TEXT,
         updated_at TEXT NOT NULL,
         PRIMARY KEY (owner_kind, owner_id, division_id, contract_id, record_id),
         CHECK((owner_kind='character' AND division_id=0) OR
               (owner_kind='corporation' AND division_id BETWEEN 0 AND 7))
     );

     CREATE TABLE IF NOT EXISTS portfolio_settings (
         owner_kind TEXT NOT NULL DEFAULT 'global'
             CHECK(owner_kind IN ('global','character','corporation')),
         owner_id INTEGER NOT NULL DEFAULT 0,
         division_id INTEGER NOT NULL DEFAULT 0,
         setting_key TEXT NOT NULL,
         setting_value TEXT,
         updated_at TEXT NOT NULL,
         PRIMARY KEY (owner_kind, owner_id, division_id, setting_key),
         CHECK((owner_kind='global' AND owner_id=0 AND division_id=0) OR
               (owner_kind='character' AND owner_id>0 AND division_id=0) OR
               (owner_kind='corporation' AND owner_id>0 AND division_id BETWEEN 0 AND 7))
     );

     CREATE INDEX IF NOT EXISTS idx_asset_snapshots_latest
         ON asset_snapshots(owner_kind, owner_id, division_id, coherent, captured_at DESC);
     CREATE INDEX IF NOT EXISTS idx_asset_items_container_type
         ON asset_snapshot_items(owner_kind, owner_id, division_id, snapshot_id,
                                 root_container_id, type_id);
     CREATE INDEX IF NOT EXISTS idx_asset_items_type
         ON asset_snapshot_items(type_id, snapshot_id);
     CREATE INDEX IF NOT EXISTS idx_trade_scope_time
         ON trade_transactions(owner_kind, owner_id, division_id, occurred_at, transaction_id);
     CREATE INDEX IF NOT EXISTS idx_trade_scope_type_time
         ON trade_transactions(owner_kind, owner_id, division_id, type_id, occurred_at);
     CREATE INDEX IF NOT EXISTS idx_contract_scope_type
         ON contract_assets(owner_kind, owner_id, division_id, type_id, completed_at);
     CREATE INDEX IF NOT EXISTS idx_orders_character_batch
         ON character_orders(character_id, state, source_import_id, last_seen_at);
     """),
    (5, "ordres corporation isoles par division wallet",
     """
     CREATE TABLE IF NOT EXISTS corporation_orders (
         corporation_id INTEGER NOT NULL CHECK(corporation_id > 0),
         order_id INTEGER NOT NULL,
         division_id INTEGER NOT NULL CHECK(division_id BETWEEN 1 AND 7),
         issued_by INTEGER,
         type_id INTEGER NOT NULL CHECK(type_id > 0),
         location_id INTEGER NOT NULL,
         region_id INTEGER,
         is_buy_order INTEGER NOT NULL CHECK(is_buy_order IN (0,1)),
         price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
         escrow_cents INTEGER CHECK(escrow_cents >= 0),
         volume_total INTEGER NOT NULL DEFAULT 0,
         volume_remain INTEGER NOT NULL DEFAULT 0,
         min_volume INTEGER NOT NULL DEFAULT 1,
         range TEXT,
         issued_at TEXT,
         duration INTEGER,
         captured_at TEXT NOT NULL,
         PRIMARY KEY (corporation_id, order_id)
     );
     CREATE INDEX IF NOT EXISTS idx_corp_orders_division_type
         ON corporation_orders(corporation_id, division_id, type_id, is_buy_order);
     """),
]


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def get_applied_versions(con):
    con.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT)""")
    rows = con.execute("SELECT version FROM schema_migrations").fetchall()
    return {r["version"] for r in rows}


def migrate(db_path=None):
    """Applique toutes les migrations non encore appliquees (idempotent)."""
    with db.connection(db_path) as con:
        applied = get_applied_versions(con)
        for version, desc, sql in MIGRATIONS:
            if version in applied:
                continue
            # executescript est auto-commit (DDL idempotent IF NOT EXISTS).
            # On insere la version juste apres ; re-run sur echec partiel est
            # retente proprement (version non inscrite -> rejoue au prochain run).
            con.executescript(sql)
            con.execute(
                "INSERT INTO schema_migrations(version, applied_at, description) VALUES (?,?,?)",
                (version, _now_iso(), desc))
    with db.connection(db_path) as con:
        return sorted(get_applied_versions(con))


if __name__ == "__main__":
    vs = migrate()
    print("Migrations appliquees:", vs)
