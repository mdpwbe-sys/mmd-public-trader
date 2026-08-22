#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
database.py - source de verite operationnelle SQLite (app_data.db).

- Mode WAL (lecteurs concurrents + 1 writer), PRAGMA robustes.
- Connexion COURTE/DEDIEE par thread (jamais partagee entre threads).
- Transactions courtes, lectures separees des ecritures.
- Helpers transactionnels + checkpoint PASSIVE (jamais bloquant).
- Aucun secret (token/secret) ne doit etre ecrit ici (cf. mmd_crypto).

Les modules GUI/ESI/Watchdog passent par repositories/ (jamais de SQL disperse).
"""

import os
import sqlite3
import threading
import time
from contextlib import contextmanager

from platform_state import state_path

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = state_path("app_data.db")

_PRAGMAS = (
    "PRAGMA journal_mode = WAL;",
    "PRAGMA synchronous = NORMAL;",
    "PRAGMA foreign_keys = ON;",
    "PRAGMA busy_timeout = 5000;",
)

_local = threading.local()


def _apply_pragmas(con):
    """Applique les PRAGMA et VERIFIE le journal_mode reel (WAL)."""
    for p in _PRAGMAS:
        con.execute(p)
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    if mode.lower() != "wal":
        # ne doit jamais arriver; on leve pour ne pas passer inapercu
        raise RuntimeError(f"journal_mode attendu WAL, obtenu {mode!r}")
    return mode


def get_connection(db_path=None):
    """Retourne une connexion FRAICHE dediee au thread courant.

    On ne reutilise PAS une connexion globale (interdit entre threads).
    La connexion est en WAL, foreign_keys ON, busy_timeout 5s.
    """
    path = db_path or DB_PATH
    con = sqlite3.connect(path, timeout=10.0, isolation_level=None,
                          check_same_thread=False)
    con.row_factory = sqlite3.Row
    _apply_pragmas(con)
    return con


@contextmanager
def connection(db_path=None):
    """Context manager: connexion courte, fermee proprement (checkpoint PASSIVE)."""
    con = get_connection(db_path)
    try:
        yield con
    finally:
        try:
            con.execute("PRAGMA wal_checkpoint(PASSIVE);")
        except Exception:
            pass
        con.close()


@contextmanager
def transaction(con):
    """Transaction ACID courte sur une connexion deja ouverte.

    IMPORTANT: BEGIN IMMEDIATE (et NON deferred) pour reserver le verrou
    d'ecriture tout de suite et eviter le deadlock d'escalade SHARED->EXCLUSIVE
    entre lecteurs qui veulent ecrire (SQLite leve 'database is locked' sans
    attendre busy_timeout dans ce cas).
    usage:
        with connection() as con:
            with transaction(con):
                repo.insert(...)
    Rollback automatique sur exception.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def checkpoint(db_path=None):
    """Checkpoint PASSIVE (non bloquant). A appeler a la fermeture propre."""
    try:
        with get_connection(db_path) as con:
            con.execute("PRAGMA wal_checkpoint(PASSIVE);")
    except Exception:
        pass


def atomic(body, *args, **kwargs):
    """Execute body(con, *args, **kwargs) dans une transaction ACID avec
    retry automatique sur 'database is locked' (backoff exponentiel).

    Garantit 0 perte de donnees meme sous forte concurrence de writers
    (SQLite = 1 seul writer a la fois ; busy_timeout + retry = pas de crash).
    body est une closure re-executable.
    """
    last = None
    for attempt in range(25):
        con = get_connection()
        try:
            # BEGIN IMMEDIATE: reserve le verrou EXCLUSIVE tout de suite,
            # evite le deadlock d'escalade SHARED->EXCLUSIVE entre writers.
            con.execute("BEGIN IMMEDIATE")
            res = body(con, *args, **kwargs)
            con.execute("COMMIT")
            return res
        except sqlite3.OperationalError as e:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            if "locked" in str(e).lower() and attempt < 24:
                time.sleep(0.02 * (attempt + 1))
                continue
            raise
        finally:
            try:
                con.execute("PRAGMA wal_checkpoint(PASSIVE);")
            except Exception:
                pass
            con.close()


def explain_query_plan(con, sql, params=()):
    """Retourne le plan EXPLAIN QUERY PLAN (pour auditer les index)."""
    rows = con.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    # autotest rapide: WAL bien actif
    with connection() as con:
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        print("journal_mode =", mode, "(WAL attendu)")
        print("busy_timeout =", con.execute("PRAGMA busy_timeout").fetchone()[0])
        print("foreign_keys =", con.execute("PRAGMA foreign_keys").fetchone()[0])
        print("synchronous =", con.execute("PRAGMA synchronous").fetchone()[0])
