#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watcher du dossier Marketlogs: comme Mmd, des qu'un export EVE
('My Orders-*.txt' ou 'The Forge-*.txt') est ecrit, on reimporte auto
(refresh de l'ecart vs le livre public) sans rescanner l'ESI privee (SSO).

Tourne en thread de fond dans le GUI. Poll toutes les ~2s le mtime du
dernier fichier pertinent -> si nouveau, declenche import_orders.
"""

import os
import time
import threading

MARKETLOGS = os.path.join(os.path.expanduser("~"), "Documents", "EVE", "logs", "Marketlogs")


def _candidate_files():
    """Fichiers surveilles: ordres PERSO (My Orders) + livres item (The Forge).
    Corporation Orders- EXCLU: EVE tronque ces exports (ordres incomplets)
    et ils incluent les ordres de tous les membres corp -> bruit. On ne
    surveille que les ordres persos."""
    if not os.path.isdir(MARKETLOGS):
        return []
    out = []
    for fn in os.listdir(MARKETLOGS):
        if fn.endswith(".txt") and (
            fn.startswith("My Orders-")
            or fn.startswith("The Forge-")
        ):
            full = os.path.join(MARKETLOGS, fn)
            try:
                out.append((os.path.getmtime(full), full))
            except OSError:
                pass
    return out


def watch(api, poll=2.0, stop_event=None):
    """Boucle de surveillance. api: instance Api du GUI (a import_orders()).
    Au demarrage, on note le dernier fichier connu -> on n'importe pas tout de
    suite (le cache de demarrage suffit). Ensuite, tout nouveau fichier -> import."""
    seen = {}
    cands = _candidate_files()
    if cands:
        cands.sort(reverse=True)
        seen["last"] = cands[0][1]
    while True:
        if stop_event and stop_event.is_set():
            break
        try:
            cands = _candidate_files()
            if cands:
                cands.sort(reverse=True)
                newest = cands[0][1]
                if newest != seen.get("last"):
                    seen["last"] = newest
                    base = os.path.basename(newest)
                    # log UI: le watchdog a intercepte un nouvel export
                    try:
                        api.log_event(f"Watchdog a intercepté l'export : {base} >>>", "watch")
                    except Exception:
                        pass
                    # --- persistance SQLite (source de verite) a l'interception ---
                    # export perso My Orders-* -> on persiste ses ordres directement.
                    # (import_orders ci-dessous fait aussi le refetch public + refresh UI
                    #  et re-persiste de facon idempotente ; double ecriture sans danger.)
                    if base.startswith("My Orders-"):
                        try:
                            import mmd_import as _imp
                            import memory_store as _ms
                            _orders, _chars = _imp.parse_export(newest)
                            _ms.persist_import(_orders, _chars)
                            try:
                                api.log_event(
                                    f"Persisté en SQLite : {len(_orders)} ordres "
                                    f"({base})", "watch")
                            except Exception:
                                pass
                        except Exception as e:
                            try:
                                api.log_event(f"SQLite indisponible ({e})", "err")
                            except Exception:
                                pass
                    # declenche l'import auto (refetch public seulement)
                    try:
                        api.import_orders(newest, None)
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(poll)


def start_watcher(api):
    """Lance le watcher en thread de fond (daemon)."""
    try:
        with open(os.path.join(os.path.dirname(__file__), "watch_debug.log"), "a", encoding="utf-8") as f:
            f.write("start_watcher called\n")
    except Exception:
        pass
    t = threading.Thread(target=watch, args=(api,), daemon=True)
    t.start()
    return t
