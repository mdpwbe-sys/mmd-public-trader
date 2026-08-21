# Refonte Mémoire — Mmd Order Manager

Date : 2026-08-06 — Auteur : Hermes Agent

## 1. Architecture existante détectée
- Stockage opérationnel = 4 JSON (volatils / non concurrents) :
  - `character_snapshots.json` → **corrompu/tronqué** (404 octets, 1 ordre partiel) → géré en tolérance d'erreur.
  - `last_scan_cache.json` → 299 ordres (`price_cents` centièmes), source complète de migration.
  - `broker_config.json` → standings BRUTS CHARACTER_THREE (Caldari State / Caldari Navy) → `character_trade_profiles`.
  - `.esi_cache.json` → cache HTTP ESI volatil (laissé tel quel, hors périmètre).
- Aucune SQLite. `mmd_vault.py` écrit déjà dans Obsidian (Historique/).
- Logique métier : `mmd_core._scan_core` / `classify` (FIFO, écart signé, doublons inter-alts).
- Points d'écriture épars : `mmd_gui.save_cache/load_cache`, `mmd_esi._save_cache`, `mmd_sso`.

## 2. Fichiers créés / modifiés
**Créés**
- `database.py` — connexion WAL/PRAGMA + `atomic()` (BEGIN IMMEDIATE + retry) + checkpoint PASSIVE.
- `migrations.py` — migrations versionnées (v1 schéma, v2 index), idempotentes.
- `repositories/{__init__,character_repository,order_repository,snapshot_repository,recommendation_repository}.py` — couche d'accès unique.
- `migrate_json.py` — migration JSON → SQLite idempotente + sauvegarde.
- `memory_store.py` — pont logique métier ↔ repos (persist_scan/import + events).
- `obsidian_writer.py` — writer Obsidian centralisé (atomique, vault TradingVault/).
- `reports.py` — rapports quotidien/hebdo + boucle d'apprentissage.
- `tests_db.py` — 18 tests obligatoires (18/18 OK).
- `REFONTE_MEMOIRE.md` — ce document.

**Modifiés**
- `mmd_gui.py` — persist_scan/import/fetch → `memory_store` (try/except : échec DB ne casse pas le scan).
- `mmd_esi.py` — `_fetch_one` journalise `esi_fetches` via repo (best-effort).
- `mmd_vault.py` — FIX `_read_history` (NameError) — voir commits précédents.

## 3. Schéma SQL final (app_data.db, WAL)
15 tables normalisées. Convention : **prix en INTEGER centièmes d'ISK** (`price_cents`),
jamais REAL/float. Timestamps UTC ISO 8601.
- ⚠️ Écart à la règle : `structures.owner_fee_rate` et
  `character_trade_profiles.faction/corporation_standing_raw` sont en **REAL**
  (standings/fee bruts tolérables en float, mais incohérent avec le docstring
  « FEES = INTEGER cents » de migrations.py — à normaliser plus tard).
- `characters`, `character_trade_profiles` (standings **bruts** uniquement)
- `character_orders` (PK `order_id`, FK `character_id`) — UPSERT `ON CONFLICT(order_id) DO UPDATE` (préserve first_seen/issued)
- `order_events` (append-only : detected/outbid/tied/completed/…)
- `market_exports`, `esi_fetches`, `market_snapshots` + `market_snapshot_orders` (PK composite)
- `structures`, `structure_access` (état par perso ; 403/404/503 → marque STALE, jamais purge)
- `trade_recommendations`, `trade_decisions`, `trade_outcomes` (boucle apprentissage)
- `schema_migrations` (versionnement)

## 4. Stratégie de migration JSON
1. Sauvegarde créée dans `_migration_backup/`.
2. Lecture tolérante : `character_snapshots.json` corrompu → `.corrupt.bak` + skip.
3. `last_scan_cache.json` (299 ordres) → `character_orders` + `market_snapshots` + `characters`.
4. `broker_config.json` → `character_trade_profiles` (standings bruts).
5. Vérification compte (299 orders / 1 snapshot) — **environnement de test**.
   ⚠️ En PROD, `migrate_json.py` n'a **pas** été exécuté : la DB réelle
   (`app_data.db`) a été peuplée **uniquement par imports live**
   (`memory_store.persist_import` via GUI/watchdog). 0 ordre y est marqué
   `'migration'` et les JSON source (`broker_config.json`, etc.) sont
   toujours présents (non renommés `.migrated.bak`). Le script de migration
   est codé + idempotent mais son run prod reste à planifier.
6. Flag `.json_migrated.flag` → **idempotent** (2e run = `already_migrated`).
7. JSON renommés en `.migrated.bak` (compat lecture conservée côté GUI en fallback).
- Aucune perte : la DB est recréable depuis les `.bak`.

## 5. Couche repository
- Connexion **courte/dédiée par thread** (`get_connection`, jamais partagée).
- Toute écriture via `db.atomic(body)` : `BEGIN IMMEDIATE` + retry sur `database is locked`
  (backoff) + `COMMIT` **dans le try** (aucune fuite de deadlock d'escalade SHARED→EXCLUSIVE).
- Lectures : `fetchall()` immédiat dans `with connection()` → pas de verrou SHARED traînant.
- Transactions atomiques snapshot (rollback → ancien snapshot intact).

## 6. Adaptation import / fetch / GUI / watchdog
- `import_orders` / `scan` / `fetch_market_prices` → `memory_store.persist_scan/import` + `record_order_event`.
- `mmd_esi._fetch_one` → `save_esi_fetch` (best-effort, jamais bloquant).
- GUI lit en fallback JSON si SQLite vide ; **aucun SQL dispersé** hors `repositories/`.
- Échec Obsidian/DB → log seul, le scan déjà calculé n'est pas annulé.

## 7. Writer Obsidian (mémoire lisible, jamais source de vérité)
- `TradingVault/` : 00_Inbox … 90_Reports + `_System/{schemas,indexes,prompts,archive}`.
- Écritures **atomiques** (tmp → fsync → `os.replace`).
- Panne Obsidian → n'affecte PAS SQLite (test 12 OK).
- Aucun secret (test 13 OK).
- 3 niveaux : `current_session.md`, `working_memory.md`, `Validated_Rules/` (jamais modifiées en silence).

## 8. Index légers pour l'agent
- `_System/indexes/*.json` (active_orders, recent_events, characters, items_summary, known_rules)
  = vues dérivées **régénérables** depuis SQLite (test 16 OK). L'agent lit l'index d'abord.

## 9. Rapports + apprentissage
- `reports.compute_daily_report()` / `compute_weekly_report()` depuis SQLite (test 15 OK).
- `learning_loop()` : reco → décision → résultat → **proposition** de règle dans
  `Proposed_Rules/` (JAMAIS validée auto ; seuil statistique requis).

## 10. Tests (tests_db.py)
**18/18 OK** : WAL actif · lecture pendant écriture · 2 writers sans perte · busy_timeout ·
rollback snapshot · migration 1× · import ne touche pas les autres · vue Tous · doublons ·
dedup order_id · STALE sur 403/404/503 · panne Obsidian · aucun secret · Decimal ·
rapport quotidien · index agent · redémarrage brutal · checkpoint PASSIVE.

## 11. Limites / incomplètements
- **BUG corrigé (2026-08-07)** : `upsert_order` lisait `volume_remain` alors que
  `mmd_import.parse_export` émet `vol_remaining` → volumes à 0 sur 100% des
  ordres en prod. Fix : fallback `vol_remaining` sur `volume_remain`/`vol_total`.
  À noter : `volume_total` = `vol_remaining` (l'export EVE ne donne que le restant).
- **Watchdog + fetch_esi_config : CÂBLÉS** (commits `a41875b` + `a730fea`, post-doc).
  Le watchdog persiste à l'interception `My Orders-*` ; `fetch_esi_config` écrit
  `character_trade_profiles` (standings BRUTS + FK parent auto-créé).
- **`migrate_json.py` non exécuté en prod** (DB peuplée par imports live) — voir §4.5.
- **Vault Obsidian + `reports.py` jamais exécutés** : `TradingVault/` est vide
  (sauf `active_orders.json`). Le writer est codé (atomique, best-effort) mais le
  livrable « mémoire lisible » n'est pas encore produit en prod.
- **`structures` / `recommendations` / `decisions` / `outcomes` = 0** en prod
  (frais Upwell + boucle d'apprentissage jamais déclenchés).
- JSON non supprimés (`.migrated.bak`) — suppression manuelle après validation utilisateur.
- Pas de nettoyage automatique des vieux snapshots (rétention = tous conservés, marqués STALE).

## 12. Sécurité
- Aucun token/secret ESI dans SQLite/JSON/Obsidian (test 13).
- Secrets gérés par Windows Credential Manager (hors périmètre DB).
- `.gitignore` exclut `*.db`, `*.db-wal`, `*.db-shm`, caches (la DB WAL volatile n'est pas versionnée).
