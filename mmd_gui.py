#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mmd Order Manager - GUI frontend (pywebview + HTML/CSS).
Pont natif vers mmd_core.scan() (fetch ESI public direct, parallele, thread-safe)
+ deep-link EVE + vault Obsidian.
"""
import os, subprocess, sys, json, threading, time
import webview
import mmd_core as core
import migrations

HERE = os.path.dirname(os.path.abspath(__file__))
GUI_DIR = os.path.join(HERE, "gui")
INDEX = os.path.join(GUI_DIR, "index.html")
from platform_state import state_path
# Persistant state dir (%APPDATA%/MMD-Trader) — survives onefile _MEI temp extraction.
LOG_PATH = state_path("mmd_history.log")
CACHE_PATH = state_path("last_scan_cache.json")  # dernier payload persiste sur disque
SPARKLINE_PATH = state_path("sparklines_cache.json")
SNAP_PATH = state_path("character_snapshots.json")
_CACHE_LOCK = threading.RLock()
_SNAPSHOT_LOCK = threading.RLock()
_SPARKLINE_LOCK = threading.RLock()

# le handle de fenetre (set au demarrage) pour push le resultat au JS
WIN = None


def save_cache(data):
    """Persiste atomiquement le dernier payload (recharge sans JSON partiel)."""
    tmp = f"{CACHE_PATH}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with _CACHE_LOCK:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CACHE_PATH)
    except Exception:
        pass
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def load_cache():
    """Lit le dernier payload persiste. Retourne dict ou None."""
    try:
        with _CACHE_LOCK:
            if os.path.exists(CACHE_PATH):
                with open(CACHE_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                    if data and isinstance(data, dict) and data.get("ok") \
                            and isinstance(data.get("orders_full"), list):
                        return data
    except Exception:
        pass
    return None


def _stable_public_orders(fetched, previous, failed_type_ids):
    """Complete seulement les types dont le fetch ESI a reellement echoue."""
    current = list(fetched or [])
    failed = {int(t) for t in (failed_type_ids or [])}
    if not failed:
        return current, False
    fallback = [o for o in (previous or [])
                if int(o.get("type_id", 0)) in failed]
    return current + fallback, bool(fallback)


def _set_count_sync_metadata(data, synced_char_ids=None, failed_type_ids=None,
                             public_orders=None, requested_type_ids=None):
    """Marque uniquement les compteurs issus d'un snapshot complet et fiable."""
    if not isinstance(data, dict) or not data.get("ok"):
        return data
    counts = data.get("orders_to_update_by_char") or {}
    synced = synced_char_ids
    if synced is None:
        synced = data.get("synced_char_ids", counts.keys())
    synced = [str(cid) for cid in synced if str(cid) in counts]
    failed = {int(tid) for tid in (failed_type_ids or [])}
    covered = {int(o.get("type_id", 0)) for o in (public_orders or [])}
    if requested_type_ids is None:
        unresolved = failed - covered
    else:
        requested = {int(tid) for tid in requested_type_ids}
        order_types = {int(o.get("type_id", 0))
                       for o in (data.get("orders_full") or [])}
        unresolved = order_types - ((requested - failed) | covered)
    affected = {
        str(o.get("char_id")) for o in (data.get("orders_full") or [])
        if int(o.get("type_id", 0)) in unresolved
    }
    data["synced_char_ids"] = [cid for cid in synced if cid not in affected]
    if unresolved:
        data["counts_unavailable_type_ids"] = sorted(unresolved)
    return data


def _dispatch_navigation(window, direction):
    """Envoie une navigation au WebView sans pouvoir tuer le thread hotkey."""
    if not window:
        return False
    step = 1 if direction > 0 else -1
    try:
        window.evaluate_js(f"window.navigateOrders({step})")
        return True
    except Exception:
        return False


WINDOW_TOPMOST_STATE = False
_IS_DRAGGING = False
_DRAG_STOP = threading.Event()
_TOPMOST_LOCK = threading.Lock()


def _window_user32():
    """Charge les appels de fenêtrage avec des signatures HWND 64 bits sûres."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    return user32


def _get_win_hwnd():
    """Récupère le HWND réactif réel de la fenêtre pywebview."""
    try:
        if WIN and hasattr(WIN, "uid"):
            import webview.platforms.winforms as wf
            inst = wf.BrowserView.instances.get(WIN.uid)
            if inst and hasattr(inst, "Handle"):
                return int(inst.Handle.ToInt64())
    except Exception:
        pass
    try:
        return int(_window_user32().FindWindowW(
            None, "EVE Market Manager") or 0) or None
    except Exception:
        pass
    return None


def _apply_window_topmost(user32, hwnd, on_top):
    """Change uniquement le Z-order: aucune coordonnee ni taille ne bouge."""
    root = user32.GetAncestor(hwnd, 3) or hwnd
    target = -1 if on_top else -2
    flags = 0x0002 | 0x0001 | 0x0010  # NOMOVE | NOSIZE | NOACTIVATE
    return bool(user32.SetWindowPos(root, target, 0, 0, 0, 0, flags))


def set_window_topmost_global(on_top=True):
    """Bascule passivement la fenêtre au premier plan absolu (HWND_TOPMOST = -1).
    Utilise SWP_NOACTIVATE (0x0010) pour NE JAMAIS voler le focus clavier/souris d'EVE Online.
    L'état n'est validé qu'après succès Win32 afin qu'un échec transitoire reste réessayable.
    """
    global WINDOW_TOPMOST_STATE
    target_state = bool(on_top)
    with _TOPMOST_LOCK:
        try:
            hwnd = _get_win_hwnd()
            if not hwnd:
                return False
            if not _apply_window_topmost(
                    _window_user32(), hwnd, target_state):
                return False
            WINDOW_TOPMOST_STATE = target_state
            return True
        except Exception:
            return False


def bring_to_front():
    """Monte la fenêtre au premier plan absolu passif sans voler le focus d'EVE."""
    set_window_topmost_global(True)


def _recent_my_orders_per_char(max_chars=3):
    """Retourne les chemins des N plus recents fichiers 'My Orders-*' distincts
    par perso (EVE ecrase les fichiers au meme timestamp, donc on prend 1
    fichier par perso pour agreger les 3 persos). None si aucun."""
    base = os.path.join(os.path.expanduser("~"), "Documents", "EVE", "logs", "Marketlogs")
    if not os.path.isdir(base):
        return None
    import mmd_import as _imp
    files = []
    seen_chars = set()
    for fn in sorted(
        [os.path.join(base, f) for f in os.listdir(base)
         if f.startswith("My Orders-") and f.endswith(".txt")],
        key=os.path.getmtime, reverse=True
    ):
        try:
            _, chars = _imp.parse_export(fn)
        except Exception:
            continue
        pk = frozenset(chars.values())
        if pk in seen_chars:
            continue
        seen_chars.add(pk)
        files.append(fn)
        if len(files) >= max_chars:
            break
    return files or None


def _is_export_file(fn):
    """True si fn est un fichier d'export PERSO (My Orders) ou livre d'un item
    (The Forge). On EXCLUT Corporation Orders- : EVE tronque/bug les exports
    corp (ordres incomplets) et les corp_market_orders sont des doublons des
    market_orders perso -> on ne surveille que les ordres persos."""
    return fn.endswith(".txt") and (
        fn.startswith("My Orders-")
        or fn.startswith("The Forge-")
    )


def _latest_my_orders_file():
    """Retourne le chemin du DERNIER fichier d'export EVE dans Marketlogs
    (My Orders- / Corporation Orders- / The Forge-), le plus recent en mtime.
    None si introuvable."""
    base = os.path.join(os.path.expanduser("~"), "Documents", "EVE", "logs", "Marketlogs")
    if not os.path.isdir(base):
        return None
    cands = []
    for fn in os.listdir(base):
        if _is_export_file(fn):
            full = os.path.join(base, fn)
            try:
                cands.append((os.path.getmtime(full), full))
            except OSError:
                pass
    if not cands:
        return None
    cands.sort(reverse=True)
    return cands[0][1]


class Api:
    def get_eve_map_data(self):
        """Expose the bundled New Eden topology without ESI or OAuth access."""
        try:
            import eve_map_service
            return {"ok": True, "data": eve_map_service.get_map_data()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def find_eve_route(self, source_id, target_id, min_security=None):
        try:
            import eve_map_service
            return {"ok": True, "data": eve_map_service.find_route(source_id, target_id, min_security)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_eve_map_live_intel(self, force=False):
        """Optional public ESI overlay; map topology stays usable on failure."""
        try:
            import eve_map_intel_service
            return eve_map_intel_service.get_live_intel(bool(force))
        except Exception as exc:
            return {"ok": False, "systems": {}, "state": "unavailable", "error": str(exc)}

    def get_eve_map_recent_kills(self, system_id):
        """Lazy zKill detail for a selected system only."""
        try:
            import eve_map_intel_service
            return eve_map_intel_service.get_recent_kills(system_id)
        except Exception as exc:
            return {"ok": False, "kills": [], "state": "unavailable", "error": str(exc)}

    def get_eve_map_combat_markers(self):
        """Return the bounded global R2Z2 feed used by the visible map only."""
        try:
            import eve_map_kill_stream
            return eve_map_kill_stream.get_recent_markers()
        except Exception as exc:
            return {"ok": False, "markers": [], "state": "unavailable", "error": str(exc)}

    def set_eve_map_combat_stream_active(self, active):
        """Avoid consuming the global zKillboard stream while the map is closed."""
        try:
            import eve_map_kill_stream
            eve_map_kill_stream.set_active(bool(active))
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_eve_map_kill_attackers(self, system_id, killmail_id):
        """Lazy attacker detail for one already-cached zKill entry."""
        try:
            import eve_map_intel_service
            return eve_map_intel_service.get_kill_attackers(system_id, killmail_id)
        except Exception as exc:
            return {"ok": False, "attackers": [], "state": "unavailable", "error": str(exc)}

    def get_eve_map_sovereignty(self, force=False):
        """Public CCP sovereignty map; cached separately from traffic/danger."""
        try:
            import eve_map_intel_service
            return eve_map_intel_service.get_sovereignty(bool(force))
        except Exception as exc:
            return {"ok": False, "systems": {}, "state": "unavailable", "error": str(exc)}

    def get_eve_map_entity_names(self, ids):
        """Resolve only IDs visible in the selected map-system panel."""
        try:
            import eve_map_intel_service
            return eve_map_intel_service.get_entity_names(ids)
        except Exception as exc:
            return {"ok": False, "names": {}, "state": "unavailable", "error": str(exc)}

    def get_eve_map_character_positions(self):
        """Optional authenticated map layer; only location-consented pilots appear."""
        try:
            import eve_map_intel_service
            return eve_map_intel_service.get_character_positions()
        except Exception as exc:
            return {"ok": False, "positions": [], "state": "unavailable", "error": str(exc)}

    def scan(self, refresh_esi=True):
        """Lance le scan dans un thread et pousse le resultat au JS.
        Source: si SSO connecte -> ordres ESI authentifies (scan_authed),
        sinon -> exports EVE logs (scan_from_logs)."""
        def worker():
            _t0 = time.time()
            try:
                import mmd_sso
                authed = mmd_sso.is_connected()
                set_status_js("busy", "Scanning…",
                              "ESI authenticated" if authed else "reading EVE logs + ESI")
                if WIN:
                    WIN.evaluate_js("logLine('Scan en cours… (EVE logs + livre public ESI)', 'info')")
                pub = None
                esi_info = None
                failed_ids = []
                ids = []
                if refresh_esi:
                    import mmd_esi
                    # type_ids connus depuis le dernier scan/snapshot -> fetch cible
                    _known = list(getattr(self, "_last_orders", []) or [])
                    _tids = sorted({int(o["type_id"]) for o in _known if o.get("type_id")})
                    if _tids:
                        ids, pub, sec, failed_ids = mmd_esi.get_live_public_for(_tids, include_failures=True)
                    else:
                        ids = mmd_esi.live_type_ids()
                        ids, pub, sec, failed_ids = mmd_esi.get_live_public_for(
                            ids, include_failures=True)
                    pub, reused_public = _stable_public_orders(
                        pub, getattr(self, "_last_public", None), failed_ids)
                    self._last_public = pub  # pour l'Import (ecart sans re-fetch)
                    esi_info = {"items": len(ids), "orders": len(pub), "sec": sec}
                    if reused_public:
                        esi_info["fallback"] = "last_valid_snapshot"
                    try:
                        import mmd_vault
                        vn = mmd_vault.update_history_from_public(pub)
                        esi_info["vault"] = vn
                    except Exception:
                        pass
                if authed:
                    data = core.scan_authed(order_books=pub)
                else:
                    data = core.scan_from_logs(public_orders=pub)
                _set_count_sync_metadata(
                    data, [] if not refresh_esi else None, failed_ids, pub, ids)
                data["esi"] = esi_info
                data["authed"] = authed
                data["sso_connected"] = authed
                data["sso_chars"] = mmd_sso.connected_chars()  # [{id, name}]
                if not data.get("ok"):
                    if WIN:
                        WIN.evaluate_js(
                            f"renderScan({json.dumps(data, ensure_ascii=True)})")
                    return
                # cache pour le Fetch prix marche (recalcule l'ecart sans SSO)
                self._last_orders = data.get("orders_full")
                self._remember_visible_orders(self._last_orders)
                if pub is not None:
                    self._last_public = pub
                save_cache(data)  # persiste pour le prochain demarrage (pas de refetch)
                try:
                    import memory_store as ms
                    sid = ms.persist_scan(data, source="scan")
                    ms.record_events_from_scan(data, sid)
                except Exception as e:
                    if WIN:
                        WIN.evaluate_js("logLine('SQLite indisponible (" + str(e) + ")', 'err')")
                # ensure_ascii=True -> tout est echappe, aucun guillemet ne casse le JS
                js = f"renderScan({json.dumps(data, ensure_ascii=True)})"
                if WIN: WIN.evaluate_js(js)
                if WIN:
                    _dur = round(time.time() - _t0, 1)
                    _n = (data.get("esi") or {}).get("orders", 0)
                    WIN.evaluate_js(f"logLine('Scan termine: {_n} ordres en {_dur}s', 'ok')")
            except Exception as e:
                if WIN: WIN.evaluate_js(f"scanError({json.dumps(str(e).replace(chr(39), chr(96)), ensure_ascii=True)})")
        threading.Thread(target=worker, daemon=True).start()
        return None

    def scan_character(self, char_id, refresh_esi=True):
        """Scan ESI RESTREINT a UN perso (ordres + transactions + assets du perso).
        Ne touche PAS aux autres persos connectes. Ne declenche PAS le cooldown
        global de 20 min (propre au bouton Refresh global)."""
        cid = str(char_id)
        name = mmd_sso._chars().get(cid, {}).get("name", cid)

        def worker():
            _t0 = time.time()
            try:
                if not mmd_sso.is_connected():
                    if WIN:
                        WIN.evaluate_js("scanError('Aucun perso ESI connecte')")
                    return
                set_status_js("busy", f"Scan {name}…", "ESI authentifie (perso unique)")
                if WIN:
                    WIN.evaluate_js(f"logLine('Scan ESI : {name}…', 'info')")
                pub = None
                failed_ids = []
                ids = []
                if refresh_esi:
                    import mmd_esi
                    import mmd_esi_orders as eo
                    orders, errs, synced = eo.fetch_orders_for_char(cid)
                    ids = sorted({int(o["type_id"]) for o in orders if o.get("type_id")})
                    if ids:
                        ids, pub, sec, failed_ids = mmd_esi.get_live_public_for(
                            ids, include_failures=True)
                    else:
                        pub, sec = [], 0
                    pub, reused = _stable_public_orders(
                        pub, getattr(self, "_last_public", None), failed_ids)
                    self._last_public = pub
                if not orders:
                    orders = eo.fetch_orders_for_char(cid)[0]
                data = core.scan_authed(order_books=pub)
                # filtre le resultat global aux ordres du perso
                if isinstance(data, dict):
                    data["orders_full"] = [o for o in data.get("orders_full", [])
                                           if str(o.get("char_id")) == cid]
                    data["orders"] = len(data["orders_full"])
                _set_count_sync_metadata(
                    data, [] if not refresh_esi else None, failed_ids, pub, ids)
                data["esi"] = {"items": len(ids), "orders": len(pub),
                               "sec": sec, "character": name}
                data["authed"] = True
                data["sso_connected"] = True
                data["sso_chars"] = mmd_sso.connected_chars()
                if not data.get("ok"):
                    if WIN:
                        WIN.evaluate_js(f"renderScan({json.dumps(data, ensure_ascii=True)})")
                    return
                self._last_orders = data.get("orders_full")
                save_cache(data)
                js = f"renderScan({json.dumps(data, ensure_ascii=True)})"
                if WIN:
                    WIN.evaluate_js(js)
                    _dur = round(time.time() - _t0, 1)
                    _n = len(pub)
                    WIN.evaluate_js(f"logLine('Scan {name} termine: {_n} ordres en {_dur}s', 'ok')")
            except Exception as e:
                if WIN:
                    WIN.evaluate_js(f"scanError({json.dumps(str(e).replace(chr(39), chr(96)), ensure_ascii=True)})")
        threading.Thread(target=worker, daemon=True).start()
        return None

    def copy_price(self, type_id, new_price_cents, char_id=None):
        """Fast-copy Mmd: copie le NOUVEAU prix au format point decimal
        (compatible nativement avec le client EVE Online sans suppression de virgule)
        ET ouvre l'item dans EVE (in-client si SSO)."""
        import mmd_price as prx
        from decimal import Decimal
        try:
            isk = prx.from_cents(int(new_price_cents))
        except (TypeError, ValueError):
            isk = Decimal("0")
        # Format point '.' pour compatibilité 100% avec le champ d'ordre EVE Online
        clip = f"{isk:.2f}"
        disp = f"{isk:.2f}".replace(".", ",")
        # copie dans le presse-papier Windows
        self.copy_text(clip)
        # log dans l'UI avec affichage lisible fr (virgule)
        if WIN:
            try:
                WIN.evaluate_js(f"logLine({json.dumps('Prix copié : ' + str(disp) + ' ISK', ensure_ascii=True)}, 'ok')")
            except Exception:
                pass
        # ouvre EVE
        self.open_item(type_id, char_id)
        return clip

    def sso_status(self):
        """Retourne la liste des persos connectes pour l'UI."""
        import mmd_sso
        return {"connected": mmd_sso.is_connected(),
                "chars": mmd_sso.connected_chars()}

    def get_trade_settings(self):
        """Sources portefeuille ESI disponibles, sans selection implicite."""
        import portfolio_service
        return portfolio_service.get_settings(force=True)

    def save_trade_settings(self, payload):
        """Valide puis persiste division corporation et conteneur personnel."""
        import portfolio_service
        return portfolio_service.save_settings(payload)

    def get_trade_workspace(self, filters=None):
        """Vue portefeuille depuis le dernier snapshot SQLite coherent."""
        import portfolio_service
        try:
            return portfolio_service.get_workspace(filters, refresh=False)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def refresh_trade_workspace(self, filters=None):
        """Synchronise les endpoints read-only puis recalcule le portefeuille."""
        import portfolio_service
        try:
            return portfolio_service.get_workspace(filters, refresh=True)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def connect_eve(self):
        """Lance le consent CCP pour le 1er perso non-connecte (ou le 1er si tous deja la).
        En boucle: tu cliques Connect EVE, autorises un perso, puis re-cliques pour le suivant.
        Le serveur local :8766 capture chaque callback en thread -> pas de freeze."""
        import mmd_sso
        def worker():
            try:
                set_status_js("busy", "Authorizing…", "login CCP in browser")
                ok, msg = mmd_sso.start_login_server()
                if WIN:
                    if ok:
                        names = [c["name"] for c in mmd_sso.connected_chars()]
                        WIN.evaluate_js(f"onSsoConnected({json.dumps(names, ensure_ascii=True)})")
                        set_status_js("", "Ready", f"EVE connecte(s): {', '.join(names)}")
                    else:
                        WIN.evaluate_js(f"scanError({json.dumps('SSO: '+str(msg), ensure_ascii=True)})")
            except Exception as e:
                if WIN: WIN.evaluate_js(f"scanError({json.dumps('SSO: '+str(e), ensure_ascii=True)})")
        threading.Thread(target=worker, daemon=True).start()
        return None

    def open_item(self, type_id, char_id=None):
        """Si SSO connecte: ouvre le market DANS le client EVE (esi-ui.open_window.v1).
        char_id: perso a utiliser (defaut = 1er connecte). Sinon: web market."""
        import mmd_sso
        if mmd_sso.is_connected():
            ok, msg = mmd_sso.open_in_client(type_id, char_id)
            if ok:
                if WIN:
                    WIN.evaluate_js(f"logLine('Ouverture marché EVE client pour type_id {type_id} ({msg})', 'info')")
                return "eve-client"
            else:
                if WIN:
                    WIN.evaluate_js(f"logLine('Échec ouverture EVE client ({msg})', 'warn')")
        try:
            os.startfile(f"https://www.fuzzwork.co.uk/market/{type_id}/")
            return "web"
        except Exception:
            pass
        try:
            os.startfile(f"eve://market/{type_id}/"); return "eve"
        except Exception:
            return False

    def get_history(self, type_id, region_id=10000002):
        """Interroge l'ESI public + SQLite (historical_market_daily) pour l'historique long-terme (>1 an).
        Enregistre dans SQLite pour accumuler les données au-delà d'un an (EVE Ref / ESI)."""
        try:
            import urllib.request
            import database as db
            import mmd_price
            import mmd_stations
            import mmd_core as core

            type_id = int(type_id)
            region_id = int(region_id)
            name = core.iname(type_id)

            # 1. Fetch ESI (données récentes ~400 jours)
            esi_records = []
            try:
                url = f"https://esi.evetech.net/latest/markets/{region_id}/history/?datasource=tranquility&type_id={type_id}"
                req = urllib.request.Request(url, headers={"User-Agent": "MmdOrderManager/1.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    raw = json.loads(resp.read().decode('utf-8'))
                    if isinstance(raw, list):
                        esi_records = raw
            except Exception:
                pass

            # 2. Insère / met à jour les données ESI dans la table SQLite historical_market_daily
            if esi_records:
                with db.connection() as con:
                    con.executemany("""
                        INSERT OR REPLACE INTO historical_market_daily 
                        (region_id, type_id, date, avg_cents, high_cents, low_cents, volume, order_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, [
                        (region_id, type_id, d["date"], 
                         int(mmd_price.to_cents(d["average"])), 
                         int(mmd_price.to_cents(d["highest"])), 
                         int(mmd_price.to_cents(d["lowest"])), 
                         int(d["volume"]), int(d["order_count"]))
                        for d in esi_records
                    ])

            # 3. Lit l'historique complet combiné depuis SQLite (support multi-années EVE Ref + ESI)
            records = []
            with db.connection() as con:
                rows = con.execute("""
                    SELECT date, avg_cents, high_cents, low_cents, volume, order_count
                    FROM historical_market_daily
                    WHERE region_id = ? AND type_id = ?
                    ORDER BY date ASC
                """, (region_id, type_id)).fetchall()
                for r in rows:
                    records.append({
                        "date": r["date"],
                        "avg_cents": r["avg_cents"],
                        "high_cents": r["high_cents"],
                        "low_cents": r["low_cents"],
                        "vol": r["volume"],
                        "orders": r["order_count"],
                    })

            # Si la base SQLite n'avait pas encore de données, on utilise les données ESI directement
            if not records and esi_records:
                esi_records.sort(key=lambda x: x["date"])
                for d in esi_records:
                    records.append({
                        "date": d["date"],
                        "avg_cents": int(mmd_price.to_cents(d["average"])),
                        "high_cents": int(mmd_price.to_cents(d["highest"])),
                        "low_cents": int(mmd_price.to_cents(d["lowest"])),
                        "vol": d["volume"],
                        "orders": d["order_count"],
                    })

            if not records:
                return {"ok": False, "error": "Aucune donnée d'historique disponible pour cet objet."}

            # 4. Calcul des Moyennes Mobiles (SMA 5j, 20j, 50j) sur tout l'historique
            n = len(records)
            for i in range(n):
                win5 = records[max(0, i-4):i+1]
                records[i]["sma5"] = int(sum(x["avg_cents"] for x in win5) / len(win5))
                win20 = records[max(0, i-19):i+1]
                records[i]["sma20"] = int(sum(x["avg_cents"] for x in win20) / len(win20))
                win50 = records[max(0, i-49):i+1]
                records[i]["sma50"] = int(sum(x["avg_cents"] for x in win50) / len(win50))

            last_30 = records[-30:] if n >= 30 else records
            highest_30 = max(x["high_cents"] for x in last_30)
            lowest_30 = min(x["low_cents"] for x in last_30)
            avg_vol_30 = int(sum(x["vol"] for x in last_30) / len(last_30))
            latest = records[-1]

            trend_short = round(((latest["sma5"] - latest["sma20"]) / latest["sma20"]) * 100, 2) if latest["sma20"] else 0.0
            trend_long = round(((latest["sma20"] - latest["sma50"]) / latest["sma50"]) * 100, 2) if latest["sma50"] else 0.0

            return {
                "ok": True,
                "type_id": type_id,
                "name": name,
                "region_id": region_id,
                "history": records,
                "stats": {
                    "latest_avg_cents": latest["avg_cents"],
                    "latest_vol": latest["vol"],
                    "highest_30_cents": highest_30,
                    "lowest_30_cents": lowest_30,
                    "avg_vol_30": avg_vol_30,
                    "trend_short_pct": trend_short,
                    "trend_long_pct": trend_long,
                }
            }
        except Exception as e:
            return {"ok": False, "error": f"Erreur lors de la récupération de l'historique: {str(e)}"}

    def set_topmost(self, flag):
        """Bascule la fenêtre au tout premier plan absolu (TopMost) ou normal."""
        return set_window_topmost_global(bool(flag))

    def open_history(self, type_id):
        """Appelé depuis JS au clic sur HIST -> récupère les données et déclenche la modale UI."""
        res = self.get_history(type_id)
        if WIN:
            WIN.evaluate_js(f"showHistoryModal({json.dumps(res)})")
        return res

    def open_log(self):
        os.startfile(LOG_PATH if os.path.exists(LOG_PATH) else HERE)

    def disconnect_eve(self):
        """Supprime toutes les donnees SSO (tokens) -> re-consent au prochain login.
        Necessaire pour activer un nouveau scope (ex: structures/citadelles)."""
        import mmd_sso
        ok = mmd_sso.disconnect_and_clear()
        if WIN:
            WIN.evaluate_js(f"onSsoDisconnected({json.dumps(ok)})")
        return ok

    def revoke_sso(self):
        """Revoque COTE ESI tous les refresh tokens connectes + supprime le
        cache local. Action destructive -> confirmee cote JS avant appel."""
        import mmd_sso
        res = mmd_sso.revoke_all()
        if WIN:
            WIN.evaluate_js(f"onSsoRevoked({json.dumps(res, ensure_ascii=True)})")
        return res

    def disconnect_char(self, char_id):
        """Deconnecte un seul personnage (supprime son token local)."""
        import mmd_sso
        ok = mmd_sso.disconnect_char(char_id)
        if WIN:
            WIN.evaluate_js(f"checkSso()")
        return ok

    def revoke_char(self, char_id):
        """Revoque les acces ESI d'un seul personnage cote CCP puis supprime son token local."""
        import mmd_sso
        ok = mmd_sso.revoke_char(char_id)
        if WIN:
            WIN.evaluate_js(f"checkSso()")
        return ok

    def start_native_drag(self):
        """Suivi 1:1 ultra-fluide du curseur materiel Win32 via GetCursorPos.
        100% en pixels physiques Win32 -> Zero saut au clic, zero decalage DPI, zero saut au relachement.
        Supporte Windows Snap Assist LIVE complet (Coins 1/4 ecran, Bords 1/2 ecran, Haut plein ecran) et auto-restore au drag.
        """
        global _IS_DRAGGING
        if _IS_DRAGGING:
            return
        _IS_DRAGGING = True
        _DRAG_STOP.clear()

        def _drag_loop():
            global _IS_DRAGGING
            try:
                import ctypes
                from ctypes import wintypes
                import time

                class POINT(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                hwnd = _get_win_hwnd()
                if not hwnd:
                    _IS_DRAGGING = False
                    return

                user32 = ctypes.windll.user32
                VK_LBUTTON = 0x01
                FLAGS = 0x0015 # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE

                # Si la fenêtre est actuellement maximisée (WS_MAXIMIZE), la restaurer automatiquement
                WS_MAXIMIZE = 0x01000000
                style = user32.GetWindowLongW(hwnd, -16)
                if (style & WS_MAXIMIZE) != 0:
                    user32.ShowWindow(hwnd, 9) # SW_RESTORE

                # Warm-up: stabilise la zone client DWM avant de capturer la position initiale
                win_rect = RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(win_rect))
                user32.SetWindowPos(hwnd, None, win_rect.left, win_rect.top, 0, 0, FLAGS)

                # 1. Position initiale du curseur matériel Win32 et du HWND
                start_pt = POINT()
                user32.GetCursorPos(ctypes.byref(start_pt))
                init_x = start_pt.x
                init_y = start_pt.y

                user32.GetWindowRect(hwnd, ctypes.byref(win_rect))
                start_win_x = win_rect.left
                start_win_y = win_rect.top

                curr_pt = POINT()
                last_dx = 0
                last_dy = 0

                v_left = user32.GetSystemMetrics(76) # SM_XVIRTUALSCREEN
                v_top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
                v_w = user32.GetSystemMetrics(78)    # SM_CXVIRTUALSCREEN
                v_h = user32.GetSystemMetrics(79)    # SM_CYVIRTUALSCREEN

                half_w = v_w // 2
                half_h = v_h // 2

                CORNER_DIST = 35
                EDGE_DIST = 20
                SWP_FLAGS_PREVIEW = 0x0014 # SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW

                snapped_zone = None

                # 2. Boucle de suivi 1000 Hz tant que le clic gauche est maintenu (avec Snap LIVE)
                while True:
                    if _DRAG_STOP.is_set():
                        break
                    is_down = (user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000) != 0
                    if not is_down:
                        break
                    user32.GetCursorPos(ctypes.byref(curr_pt))
                    last_dx = curr_pt.x - init_x
                    last_dy = curr_pt.y - init_y

                    cx = curr_pt.x
                    cy = curr_pt.y

                    # Détection des zones de Snap LIVE pendant le déplacement
                    current_zone = None
                    if cx <= (v_left + CORNER_DIST) and cy <= (v_top + CORNER_DIST):
                        current_zone = "top-left"
                    elif cx >= (v_left + v_w - CORNER_DIST) and cy <= (v_top + CORNER_DIST):
                        current_zone = "top-right"
                    elif cx <= (v_left + CORNER_DIST) and cy >= (v_top + v_h - CORNER_DIST):
                        current_zone = "bottom-left"
                    elif cx >= (v_left + v_w - CORNER_DIST) and cy >= (v_top + v_h - CORNER_DIST):
                        current_zone = "bottom-right"
                    elif cy <= (v_top + EDGE_DIST):
                        current_zone = "top"
                    elif cx <= (v_left + EDGE_DIST):
                        current_zone = "left"
                    elif cx >= (v_left + v_w - EDGE_DIST):
                        current_zone = "right"

                    if current_zone:
                        if snapped_zone != current_zone:
                            snapped_zone = current_zone
                            if current_zone == "top-left":
                                user32.SetWindowPos(hwnd, None, v_left, v_top, half_w, half_h, SWP_FLAGS_PREVIEW)
                            elif current_zone == "top-right":
                                user32.SetWindowPos(hwnd, None, v_left + half_w, v_top, half_w, half_h, SWP_FLAGS_PREVIEW)
                            elif current_zone == "bottom-left":
                                user32.SetWindowPos(hwnd, None, v_left, v_top + half_h, half_w, half_h, SWP_FLAGS_PREVIEW)
                            elif current_zone == "bottom-right":
                                user32.SetWindowPos(hwnd, None, v_left + half_w, v_top + half_h, half_w, half_h, SWP_FLAGS_PREVIEW)
                            elif current_zone == "top":
                                user32.ShowWindow(hwnd, 3) # SW_MAXIMIZE
                            elif current_zone == "left":
                                user32.SetWindowPos(hwnd, None, v_left, v_top, half_w, v_h, SWP_FLAGS_PREVIEW)
                            elif current_zone == "right":
                                user32.SetWindowPos(hwnd, None, v_left + half_w, v_top, half_w, v_h, SWP_FLAGS_PREVIEW)
                    else:
                        if snapped_zone is not None:
                            if snapped_zone == "top":
                                user32.ShowWindow(hwnd, 9) # SW_RESTORE
                            snapped_zone = None
                        user32.SetWindowPos(hwnd, None, start_win_x + last_dx, start_win_y + last_dy, 0, 0, FLAGS)

                    time.sleep(0.001)

            except Exception:
                pass
            finally:
                _IS_DRAGGING = False

        threading.Thread(target=_drag_loop, daemon=True).start()

    def stop_native_drag(self):
        """Annule tout suivi curseur restant avant un changement de modale."""
        _DRAG_STOP.set()
        return True

    def toggle_maximize_window(self):
        """Bascule entre fenêtre agrandie (Maximize) et taille normale (Restore)."""
        try:
            hwnd = _get_win_hwnd()
            if hwnd:
                import ctypes
                user32 = ctypes.windll.user32
                WS_MAXIMIZE = 0x01000000
                style = user32.GetWindowLongW(hwnd, -16) # GWL_STYLE
                if (style & WS_MAXIMIZE) != 0:
                    user32.ShowWindow(hwnd, 9) # SW_RESTORE
                else:
                    user32.ShowWindow(hwnd, 3) # SW_MAXIMIZE
        except Exception:
            pass

    def minimize_window(self):
        """Reduit la fenetre dans la barre des taches."""
        if WIN:
            try:
                if hasattr(WIN, "minimize"):
                    WIN.minimize()
                else:
                    import ctypes
                    hwnd = ctypes.windll.user32.FindWindowW(None, "EVE Market Manager")
                    if hwnd:
                        ctypes.windll.user32.ShowWindow(hwnd, 6)
            except Exception:
                pass

    def close_window(self):
        """Ferme proprement la fenêtre et libère le fichier de verrou .running.lock."""
        global _RUNNING_LOCK_FH
        try:
            if _RUNNING_LOCK_FH:
                try:
                    import msvcrt
                    msvcrt.locking(_RUNNING_LOCK_FH.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
                try:
                    _RUNNING_LOCK_FH.close()
                except Exception:
                    pass
                _RUNNING_LOCK_FH = None
            if os.path.exists(_RUNNING_LOCK_PATH):
                try:
                    os.remove(_RUNNING_LOCK_PATH)
                except Exception:
                    pass
        except Exception:
            pass
        if WIN:
            try:
                WIN.destroy()
            except Exception:
                os._exit(0)

    def set_window_topmost(self, on_top=True):
        """Bascule la fenêtre au premier plan absolu (HWND_TOPMOST passif)."""
        return set_window_topmost_global(on_top)

    def resize_window(self, w, h):
        """Redimensionne la fenêtre (largeur, hauteur)."""
        if WIN:
            try:
                WIN.resize(int(w), int(h))
            except Exception:
                pass

    def reset_database(self):
        """Supprime la base app_data.db (reset a zero) apres fermeture des
        connexions, puis recree le schema vide via migrations.migrate().
        Vide aussi le cache de scan (derniere synchro). Action destructive ->
        confirmee cote JS."""
        import database as db
        import migrations
        try:
            con = db.get_connection()
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except Exception:
                pass
            con.close()
        except Exception:
            pass
        for suffix in ("", "-wal", "-shm"):
            p = db.DB_PATH + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        try:
            migrations.migrate(db.DB_PATH)
        except Exception:
            pass
        try:
            if os.path.exists(CACHE_PATH):
                os.remove(CACHE_PATH)
        except Exception:
            pass
        if WIN:
            WIN.evaluate_js("onDatabaseReset()")
        return {"ok": True}

    def save_sparklines(self, data):
        """Sauvegarde atomique (.tmp + os.replace) de la progression historique des sparklines sur le disque."""
        tmp = f"{SPARKLINE_PATH}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with _SPARKLINE_LOCK:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, SPARKLINE_PATH)
            return True
        except Exception:
            return False
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def get_sparklines(self):
        """Lit la progression historique des sparklines depuis le disque."""
        try:
            with _SPARKLINE_LOCK:
                if os.path.exists(SPARKLINE_PATH):
                    with open(SPARKLINE_PATH, "r", encoding="utf-8") as f:
                        return json.load(f)
        except Exception:
            pass
        return {}

    def import_orders(self, file_path, sel_char_id=None):
        """Importe un/plusieurs fichiers d'export EVE ('Export your market orders').
        - file_path peut etre un chemin unique, ou une liste (agregation multi-persos).
        - Par defaut (file_path=None depuis Import auto), on agrege les 3 plus
          recents 'My Orders-*' distincts par perso (EVE ecrase les fichiers au
          meme timestamp, donc on prend 1 fichier par perso).
        Le snapshot de chaque perso est stocke dans self._snapshots[char_id]
        (jamais ecrase par un autre perso) et persiste sur disque. La vue
        agregee (Tous) = concat de tous les snapshots.
        Verifie que le fichier correspond au perso selectionne (chip actif)
        ou a un perso connecte SSO. Refuse + alerte si mismatch.
        Calcule l'ecart via le livre public ESI courant si disponible."""
        import mmd_import as imp
        import mmd_sso

        # --- routeur : livre public 'The Forge-<item>*.txt' ---
        # C'est un LIVRE PUBLIC (pas un export d'ordres perso) -> popup marge nette,
        # pas un import d'ordres. On le traite immediatement et on sort.
        if isinstance(file_path, (str, bytes, os.PathLike)) and str(file_path).strip() \
                and os.path.basename(str(file_path)).startswith("The Forge-"):
            self.margin_from_book(str(file_path))
            return None

        # garde: file_path doit etre une string valide (pas un dict/None)
        if isinstance(file_path, (tuple, list)):
            files = [f for f in file_path if isinstance(f, (str, bytes, os.PathLike)) and str(f).strip()]
        else:
            if not isinstance(file_path, (str, bytes, os.PathLike)) or not str(file_path).strip():
                # Import auto: agrege les 3 plus recents My Orders distincts par perso
                files = _recent_my_orders_per_char() or []
            else:
                files = [str(file_path)]

        if not files:
            if WIN:
                WIN.evaluate_js("setStatus('err','Erreur','Aucun fichier My Orders trouve')")
            return None

        # Union monotone disque + memoire + dernier Refresh : aucune autre cle
        # personnage ne peut disparaitre lors du remplacement de l'importe.
        self._remember_visible_orders(getattr(self, "_last_orders", None))

        imported_chars = []
        for fp in files:
            if WIN:
                WIN.evaluate_js(
                    f"logLine({json.dumps('« ' + os.path.basename(fp) + ' » reçu — import du prix du marché en cours >>>', ensure_ascii=True)}, 'import')"
                )
            try:
                orders, ch = imp.parse_export(fp)
            except Exception as e:
                if WIN:
                    WIN.evaluate_js(f"importRejected({json.dumps(str(e), ensure_ascii=True)})")
                return None
            sso_chars = mmd_sso.connected_chars() if mmd_sso.is_connected() else []
            # on importe CHAQUE perso du fichier comme un snapshot distinct
            # (un fichier My Orders ne contient qu'un perso, mais on reste generique)
            for cid, cname in ch.items():
                corders = [o for o in orders if int(o["char_id"]) == int(cid)]
                if not corders:
                    continue
                self._snapshots[int(cid)] = {
                    "character_id": int(cid),
                    "character_name": cname,
                    "imported_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                    "source_file": os.path.basename(fp),
                    "orders": corders,
                }
                if cid not in imported_chars:
                    imported_chars.append(cid)

        # persiste sur disque
        self._save_snapshots()

        # vue agregee: tous les snapshots
        all_orders = [o for snap in self._snapshots.values() for o in snap["orders"]]
        chars_seen = {int(cid): snap["character_name"] for cid, snap in self._snapshots.items()}

        sso_chars = mmd_sso.connected_chars() if mmd_sso.is_connected() else []
        # NOTE: import multi-persos => on garde la vue globale (Tous) par defaut.
        # si un seul perso vient d'etre importe et que c'est le 1er connu, le JS
        # auto-selectionne. Sinon Tous reste (pour voir les doublons inter-persos).
        sel = sel_char_id if sel_char_id is not None else None
        ok, reason, matched = imp.verify_character(all_orders, chars_seen, sel, sso_chars)
        if not ok:
            if WIN:
                WIN.evaluate_js(f"importRejected({json.dumps(reason, ensure_ascii=True)})")
            return None
        # Refetch le livre public (concurrence) pour calculer l'ecart frais.
        # On ne recupere QUE les items qu'on a reellement en ordre (pas tout le
        # db Mmd verrouille ni les 465 items live) -> bien plus rapide.
        pub = None
        failed_ids = []
        type_ids = sorted({
            int(o.get("type_id")) for o in all_orders if o.get("type_id")
        })
        try:
            import mmd_esi
            if WIN:
                WIN.evaluate_js("logLine('Récupération du livre public (ESI) en cours…', 'import')")
            def _prog(done, total):
                if WIN and done % max(1, total // 10) == 0:
                    WIN.evaluate_js(
                        f"logLine('Livre public ESI : {done}/{total} items…', 'import')")
            ids, pub, sec, failed_ids = mmd_esi.get_live_public_for(type_ids, progress=_prog, include_failures=True)
            pub, reused_public = _stable_public_orders(
                pub, getattr(self, "_last_public", None), failed_ids)
            if WIN:
                WIN.evaluate_js(
                    f"logLine('Livre public ESI reçu : {len(pub)} ordres ({sec}s)', 'import')")
        except Exception as e:
            if WIN:
                WIN.evaluate_js(f"logLine('Livre public ESI indisponible ({e}); écart = –', 'import')")
            pub = getattr(self, "_last_public", None)
            failed_ids = type_ids
        # Toujours conserver TOUS les ordres de tous les persos dans la vue globale (multi-persos)
        data = imp.build_payload(all_orders, None, pub)
        if pub is None:
            data["synced_char_ids"] = []
            data["counts_unavailable_type_ids"] = type_ids
        else:
            _set_count_sync_metadata(
                data, imported_chars, failed_ids, pub, type_ids)
        if imported_chars:
            data["imported_char_id"] = imported_chars[0]
        elif sel_char_id is not None:
            data["imported_char_id"] = sel_char_id

        data["imported_file"] = ", ".join(
            os.path.basename(self._snapshots[c]["source_file"]) for c in imported_chars
        ) if imported_chars else "cache"
        # sso_chars & snapshot chars : union de tous les persos connus (SSO + snapshots + scan)
        all_known = {}
        for cid, cname in chars_seen.items():
            all_known[int(cid)] = cname
        for c in sso_chars:
            all_known[int(c["id"])] = c["name"]
        for snap in self._snapshots.values():
            all_known[int(snap["character_id"])] = snap["character_name"]

        data["sso_chars"] = [{"id": cid, "name": n} for cid, n in all_known.items()]
        data["sso_connected"] = bool(sso_chars)
        data["snapshot_chars"] = list(all_known.values())
        data["characters"] = list(all_known.values())
        self._last_orders = data.get("orders_full")
        if pub is not None:
            self._last_public = pub
        # cache complet (pour redemarrage sans refetch) = payload agrege
        save_cache(data)
        # --- persistance SQLite (source de verite durable) ---
        try:
            import memory_store as ms
            snap_id = ms.persist_import(all_orders, {str(c): n for c, n in chars_seen.items()})
            ms.record_events_from_scan(data, snap_id)
            if WIN:
                WIN.evaluate_js("logLine('Persiste en SQLite (" + str(len(all_orders)) + " ordres, snapshot " + str(snap_id) + ")', 'import')")
        except Exception as e:
            if WIN:
                WIN.evaluate_js("logLine('SQLite indisponible (" + str(e) + ")', 'import')")
        js = f"renderScan({json.dumps(data, ensure_ascii=True, default=str)})"
        if WIN:
            WIN.evaluate_js(js)
        return None

    # ---- cache multi-persos (snapshots indexés par character_id) ----
    # SNAP_PATH est defini au niveau module (state_path, persistant).

    def _load_snapshots(self):
        """Fusionne le disque avec la memoire sans supprimer un perso connu."""
        with _SNAPSHOT_LOCK:
            memory = {
                int(cid): snap for cid, snap in
                (getattr(self, "_snapshots", {}) or {}).items()
                if isinstance(snap, dict) and "orders" in snap
            }
            try:
                if os.path.exists(self.SNAP_PATH):
                    with open(self.SNAP_PATH, encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        disk = {
                            int(cid): snap for cid, snap in loaded.items()
                            if isinstance(snap, dict) and "orders" in snap
                        }
                        disk.update(memory)
                        memory = disk
            except Exception:
                pass
            self._snapshots = memory

    def _remember_visible_orders(self, orders):
        """Rend durable l'union connue; une vue partielle ne retire aucune cle."""
        try:
            import mmd_import as imp
            with _SNAPSHOT_LOCK:
                self._load_snapshots()
                if orders:
                    self._snapshots = imp.merge_visible_orders(
                        self._snapshots, orders)
                    self._save_snapshots()
            return True
        except Exception:
            return False

    def _save_snapshots(self):
        """Persiste les snapshots sur disque (empreinte du fichier source pour
        eviter double-import, mais on ne supprime JAMAIS les fichiers EVE)."""
        tmp = f"{self.SNAP_PATH}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with _SNAPSHOT_LOCK:
                self._load_snapshots()
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._snapshots, f, ensure_ascii=False, default=str)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.SNAP_PATH)
        except Exception:
            pass
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def get_snapshot_chars(self):
        """Retourne la liste des persos connus (pour le bouton Tous/multi)."""
        self._load_snapshots()
        return [{"id": cid, "name": snap["character_name"]}
                for cid, snap in self._snapshots.items()]

    def settings(self):
        # Ouvre le dossier de l'application (plus de reference au vault Obsidian perso)
        os.startfile(HERE)

    def fetch_market_prices(self):
        """Refetch uniquement le livre public (concurrence) et recalcule l'ecart
        sur les ordres perso deja charges (cache). N'appelle PAS l'ESI privee
        (SSO) -> economise les tokens. Like Mmd: prix marche separe du scan."""
        import mmd_core as core
        import mmd_esi
        import mmd_sso
        orders = getattr(self, "_last_orders", None)
        if not orders:
            if WIN:
                WIN.evaluate_js("setStatus('err','Erreur','Scanne ou importe d\\u2019abord tes ordres')")
            return None
        try:
            set_status_js("busy", "Fetch prix…", "livre public ESI")
            # ne recupere que les items qu'on a deja en ordre (pas tout le db)
            type_ids = sorted({int(o["type_id"]) for o in orders if o.get("type_id")})
            if type_ids:
                ids, pub, sec, failed_ids = mmd_esi.get_live_public_for(type_ids, include_failures=True)
            else:
                ids = mmd_esi.live_type_ids()
                ids, pub, sec, failed_ids = mmd_esi.get_live_public_for(
                    ids, include_failures=True)
            pub, reused_public = _stable_public_orders(
                pub, getattr(self, "_last_public", None), failed_ids)
            self._last_public = pub
            # on ne garde que les champs minimum pour _scan_core depuis orders_full
            mini = [{
                "order_id": o.get("order_id"), "type_id": o["type_id"],
                "char_id": o.get("char_id"), "char_name": o.get("char_name"),
                "station_id": o["station_id"], "side": o["side"],
                "price": o.get("price", o.get("price_cents", 0) / 100.0),
                "vol_remaining": o.get("vol_remaining", 0),
                "issued": o.get("issued", ""),
            } for o in orders]
            data = core._scan_core(mini, pub, "Fetch prix marché (public only)")
            _set_count_sync_metadata(data, [], failed_ids, pub, ids)
            data["esi"] = {"items": len(ids), "orders": len(pub), "sec": sec}
            if reused_public:
                data["esi"]["fallback"] = "last_valid_snapshot"
            data["authed"] = mmd_sso.is_connected()
            data["sso_connected"] = mmd_sso.is_connected()
            data["sso_chars"] = mmd_sso.connected_chars()
            save_cache(data)
            try:
                import memory_store as ms
                sid = ms.persist_scan(data, source="fetch_price")
                ms.record_events_from_scan(data, sid)
            except Exception as e:
                if WIN:
                    WIN.evaluate_js("logLine('SQLite indisponible (" + str(e) + ")', 'err')")
            js = f"renderScan({json.dumps(data, ensure_ascii=True)})"
            if WIN:
                WIN.evaluate_js(js)
        except Exception as e:
            if WIN:
                WIN.evaluate_js(f"scanError({json.dumps(str(e), ensure_ascii=True)})")
        return None

    def open_file_dialog(self):
        """Dialogue natif pywebview pour choisir le fichier d'export EVE.
        Retourne le chemin (str) ou '' si annule/erreur."""
        import webview
        win = webview.windows[0] if webview.windows else None
        if not win:
            return ""
        try:
            # pas de file_types (le filtre peut faire planter le retour en dict
            # sur certaines versions de pywebview) -> on accepte tous fichiers
            result = win.create_file_dialog(webview.OPEN_DIALOG)
        except Exception as e:
            try:
                with open(state_path("dialog_debug.log"), "a", encoding="utf-8") as f:
                    f.write(f"EXC: {e!r}\n")
            except Exception:
                pass
            return ""
        # debug: log le retour brut pour comprendre le format
        try:
            with open(state_path("dialog_debug.log"), "a", encoding="utf-8") as f:
                f.write(f"RESULT type={type(result).__name__} val={result!r}\n")
        except Exception:
            pass
        # pywebview renvoie une liste [path] ou une string ; on normalise
        if isinstance(result, (list, tuple)):
            if not result:
                return ""
            result = result[0]
        if isinstance(result, dict):
            # certaines versions renvoient un dict -> on tente d'extraire un chemin
            for k in ("file", "path", "result", "value", "selected"):
                if k in result and isinstance(result[k], (str, bytes, os.PathLike)):
                    return str(result[k])
            # parfois le chemin est la 1ere valeur
            for v in result.values():
                if isinstance(v, (str, bytes, os.PathLike)) and (".txt" in str(v) or ".csv" in str(v)):
                    return str(v)
            return ""
        if not isinstance(result, (str, bytes, os.PathLike)):
            return ""
        return str(result)

    def copy_text(self, text):
        text_str = str(text).strip()
        # 1. Native Windows ctypes API 64-bit (méthode directe & instantanée sans découpage)
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            kernel32.GlobalAlloc.restype = ctypes.c_void_p
            kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
            kernel32.GlobalLock.restype = ctypes.c_void_p
            kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
            kernel32.GlobalUnlock.restype = wintypes.BOOL
            kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            user32.SetClipboardData.restype = ctypes.c_void_p
            user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
            user32.OpenClipboard.restype = wintypes.BOOL
            user32.OpenClipboard.argtypes = [ctypes.c_void_p]
            user32.EmptyClipboard.restype = wintypes.BOOL
            user32.EmptyClipboard.argtypes = []
            user32.CloseClipboard.restype = wintypes.BOOL
            user32.CloseClipboard.argtypes = []

            buf = ctypes.create_unicode_buffer(text_str)
            byte_len = ctypes.sizeof(buf)
            if user32.OpenClipboard(None):
                user32.EmptyClipboard()
                h = kernel32.GlobalAlloc(0x2000, byte_len)
                p = kernel32.GlobalLock(h)
                if p:
                    ctypes.memmove(p, buf, byte_len)
                    kernel32.GlobalUnlock(h)
                    user32.SetClipboardData(13, h)
                user32.CloseClipboard()
                return True
        except Exception:
            pass

        # 2. PowerShell via pipe stdin (fallback anti-découpage par virgule)
        try:
            import subprocess
            res = subprocess.run(
                ['powershell', '-NoProfile', '-Command', '$input | Set-Clipboard'],
                input=text_str.encode('utf-8'),
                creationflags=0x08000000, # CREATE_NO_WINDOW
                timeout=3
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass
        return False

    def exit(self):
        webview.windows[0].destroy()

    def log_event(self, msg, kind="info"):
        """Push une ligne dans le log UI bas d'ecran (appelle window.logLine)."""
        if WIN:
            try:
                WIN.evaluate_js(f"logLine({json.dumps(str(msg), ensure_ascii=True)}, {json.dumps(kind, ensure_ascii=True)})")
            except Exception:
                pass

    def get_broker_config(self):
        """Retourne la config standings/skills (pour pre-remplir le panneau marge)."""
        import mmd_margin as m
        return m.load_config()

    def save_broker_config(self, cfg):
        """Sauvegarde la config standings/skills (JSON + miroir Obsidian)."""
        import mmd_margin as m
        try:
            cfg = json.loads(cfg) if isinstance(cfg, str) else cfg
        except Exception:
            cfg = {}
        return m.save_config(cfg)

    def fetch_esi_config(self):
        """Auto-remplit standings/skills depuis l'ESI (scope standings+skills)."""
        import mmd_margin as m
        try:
            cfg = m.fetch_esi_config()
            if WIN:
                WIN.evaluate_js("logLine('Config frais auto-remplie depuis EVE (ESI standings/skills)', 'import')")
            return {"ok": True, "config": cfg}
        except Exception as e:
            if WIN:
                WIN.evaluate_js(f"logLine('Auto-config ESI echouee: {e}', 'err')")
            return {"ok": False, "error": str(e)}

    def get_station_config(self):
        """Retourne la paire BUY/SELL station configuree (+ labels resolus)."""
        import mmd_margin as m
        import mmd_stations as stt
        cfg = m.load_config()
        buy = cfg.get("buy_station") or 0
        sell = cfg.get("sell_station") or 0
        return {
            "buy_station": buy,
            "sell_station": sell,
            "buy_label": stt.resolve_name(buy) if buy else "",
            "sell_label": stt.resolve_name(sell) if sell else "",
        }

    def save_station_config(self, payload):
        """Persiste la paire BUY/SELL station (JSON broker_config)."""
        import mmd_margin as m
        try:
            payload = json.loads(payload) if isinstance(payload, str) else payload
        except Exception:
            payload = {}
        cfg = m.load_config()
        cfg["buy_station"] = int(payload.get("buy_station") or 0)
        cfg["sell_station"] = int(payload.get("sell_station") or 0)
        m.save_config(cfg)
        return self.get_station_config()

    def firstrun_status(self):
        """True si le .env (CLIENT_ID) est deja configure."""
        import os as _os
        from platform_state import state_path
        env = state_path(".env")
        if not _os.path.exists(env):
            return {"configured": False}
        try:
            with open(env, encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("CLIENT_ID") and "=" in line:
                        v = line.split("=", 1)[1].strip()
                        if v:
                            return {"configured": True}
        except Exception:
            pass
        return {"configured": False}

    def save_firstrun_config(self, payload):
        """Option B : premier lancement -> ecrit le .env (CLIENT_ID/SECRET du user)
        puis declenche la connexion CCP pour peupler l'app."""
        import os as _os
        from platform_state import state_path
        try:
            payload = json.loads(payload) if isinstance(payload, str) else payload
        except Exception:
            payload = {}
        cid = (payload.get("client_id") or "").strip()
        sec = (payload.get("client_secret") or "").strip()
        if not cid or not sec:
            return {"ok": False, "error": "CLIENT_ID et CLIENT_SECRET requis"}
        env = state_path(".env")
        # preserve callback + scopes from .env.example defaults if absent
        lines = [
            f"CLIENT_ID={cid}",
            f"CLIENT_SECRET={sec}",
            "CALLBACK_URL=http://127.0.0.1:8766/callback",
        ]
        _os.makedirs(_os.path.dirname(env), exist_ok=True)
        with open(env, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        # refresh module cache so get_login_url picks up the new client id
        import importlib
        import mmd_sso
        importlib.reload(mmd_sso)
        return {"ok": True}

    def search_station(self, query):
        """Recherche station/citadelle par nom via ESI /universe/ids (anonyme).
        Retourne [{id, name, category}] trie par pertinence."""
        import urllib.request, urllib.error, json as _json
        q = (query or "").strip()
        if len(q) < 2:
            return []
        try:
            url = "https://esi.evetech.net/v2/universe/ids/?datasource=tranquility"
            req = urllib.request.Request(url, data=_json.dumps([q]).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = _json.loads(r.read().decode("utf-8"))
            out = []
            for cat in ("stations", "structures"):
                for e in data.get(cat, []):
                    out.append({"id": int(e["id"]), "name": e["name"], "category": cat[:-1]})
            return out[:20]
        except Exception:
            return []

    def resolve_station(self, station_id):
        """Resout un location_id en (name, systeme, region) via SDE locale."""
        import mmd_stations as stt
        try:
            sid = int(station_id)
        except (ValueError, TypeError):
            return {"id": station_id, "name": str(station_id), "system": None, "region": None}
        sysid, reg, name = stt.resolve(sid)
        return {"id": sid, "name": name, "system": sysid, "region": reg}

    def margin_from_book(self, path, order_station_id=None):
        """Calcule la marge nette d'un livre public 'The Forge-<item>*.txt'
        et pousse le popup showMargin au JS.
        order_station_id = station ou est l'ordre de l'utilisateur (achat) ;
        si fourni, la marge est calculee achat@order_station / vente@station_cible.
        Corrige le bug +63569% (BUY Jita vs ordre Perimeter)."""
        import mmd_margin as m
        try:
            rows, tid, name = m.parse_market_book(path)
            cfg = m.load_config()
            res = m.compute_margin(rows, cfg,
                                   station_pref=cfg.get("sell_station") or None,
                                   order_station_id=cfg.get("buy_station") or order_station_id)
            res["item_name"] = name
            res["type_id"] = tid
            res["source_file"] = os.path.basename(path)
            res["book_path"] = path
            js = f"showMargin({json.dumps(res, ensure_ascii=True)})"
            if WIN:
                bring_to_front()  # Pousse au 1er plan absolu au-dessus du jeu EVE Online client (style Mmd)
                WIN.evaluate_js(js)
                _tid = int(tid) if tid else None
                WIN.evaluate_js("logLine(" + json.dumps("Demande marge nette : " + name + " — calcul en cours", ensure_ascii=True) + ", 'import', " + json.dumps(_tid) + ")")
            return res
        except Exception as e:
            if WIN:
                bring_to_front()
                WIN.evaluate_js(f"showMargin({{'ok': false, 'reason': {json.dumps(str(e), ensure_ascii=True)}}})")
            return None

    def get_cached_scan(self):
        """Retourne le dernier payload persiste (sans refetch). Pour le
        demarrage de l'app: on recharge les dernieres donnees importees/refreshed."""
        data = load_cache() or {}
        if data.get("ok") and data.get("orders_full"):
            self._last_orders = list(data["orders_full"])
            self._remember_visible_orders(self._last_orders)
        return data

    def latest_my_orders_file(self):
        """Retourne le chemin du dernier fichier 'My Orders-*.txt' de Marketlogs.
        Pour l'Import automatique (pas de dialogue fichier)."""
        return _latest_my_orders_file() or ""


def set_status_js(state, txt, sub):
    if WIN:
        t = json.dumps(txt, ensure_ascii=True)
        s = json.dumps(sub or "", ensure_ascii=True)
        WIN.evaluate_js(f"setStatus({json.dumps(state or '')}, {t}, {s})")


import uuid

try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False


def _read_meta(meta_path):
    """Lit les metadonnees du verrou (JSON). None si absent/illisible."""
    try:
        with open(meta_path) as f:
            return json.load(f)
    except Exception:
        return None


def _write_meta(meta_path, pid=None):
    """Ecrit/rafraichit les metadonnees du verrou (JSON)."""
    pid = pid or os.getpid()
    data = {
        "pid": pid,
        "instance_id": getattr(_write_meta, "_iid", None) or str(uuid.uuid4()),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "heartbeat": time.time(),
    }
    _write_meta._iid = data["instance_id"]
    if HAVE_PSUTIL:
        try:
            p = psutil.Process(pid)
            data["process_create_time"] = p.create_time()
            data["executable"] = p.exe()
        except Exception:
            pass
    try:
        with open(meta_path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _owner_state(data):
    """Etat du detenteur du lock: 'dead' | 'recycled' | 'zombie' | 'alive'.
    - dead:     PID inexistant -> lock stale, on supprime.
    - recycled: PID existe mais create_time/exe ne matchent pas (PID reuse par
                un AUTRE programme) -> lock stale, on supprime.
    - zombie:   PID existe + identite match mais heartbeat expire -> instance
                bloquee/non responsive -> on tente un arret propre puis on reprend.
    - alive:    PID existe + identite match + heartbeat recent -> vraie 2e
                instance -> on refuse le demarrage.
    """
    if not data or "pid" not in data:
        return "dead"
    pid = data["pid"]
    if not HAVE_PSUTIL:
        # fallback: os.kill(0) (pas d'identite/health, mais evite le blocage
        # sur un PID reellement mort)
        try:
            os.kill(pid, 0)
            return "alive"
        except OSError:
            return "dead"
    try:
        p = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return "dead"
    # identite: create_time (Windows recycle les PID) + executable
    if "process_create_time" in data:
        try:
            if abs(p.create_time() - data["process_create_time"]) > 1.0:
                return "recycled"
        except Exception:
            pass
    if "executable" in data:
        try:
            if p.exe().lower() != data["executable"].lower():
                return "recycled"
        except Exception:
            pass
    # sante: heartbeat recent ?
    hb = data.get("heartbeat", 0)
    if time.time() - hb > 15:
        return "zombie"
    return "alive"


def _heartbeat_loop(meta_path):
    """Thread daemon: met a jour le heartbeat du lock toutes les 5s."""
    while True:
        time.sleep(5)
        try:
            _write_meta(meta_path)
        except Exception:
            break


def main():
    global WIN
    # Verrou mono-instance ROBUSTE (PID-aware + identity-aware + health-aware).
    # .running.lock = fichier verrouille par msvcrt (advisory).
    # .running.meta.json = metadonnees (pid, process_create_time, executable,
    #   instance_id, heartbeat) pour distinguer PID mort / PID recycle / meme
    #   instance responsive / meme instance zombie.
    # Comportement:
    #   lock absent -> demarrer
    #   PID mort / recycle -> lock stale -> supprimer -> demarrer
    #   meme instance + heartbeat recent -> vraie 2e instance -> refuser
    #   meme instance + heartbeat expire -> zombie -> tenter arret propre,
    #     puis supprimer le lock -> demarrer
    lock_path = state_path(".running.lock")
    meta_path = state_path(".running.meta.json")
    state = _owner_state(_read_meta(meta_path))
    if state == "alive":
        try:
            pid = _read_meta(meta_path).get("pid", "?")
        except Exception:
            pid = "?"
        print("[Mmd] Une instance tourne deja (PID %s, responsive). Arret." % pid)
        return
    if state in ("dead", "recycled", "zombie"):
        # libere le lock (process mort / recycle / bloque)
        for f in (lock_path, meta_path):
            try:
                os.remove(f)
            except Exception:
                pass
    # prend le verrou
    try:
        lock_fd = open(lock_path, "w")
        import msvcrt
        msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
    except PermissionError:
        # le lock est deja verrouille par un AUTRE process (instance en cours
        # ou residuelle non terminee). On ne peut pas le prendre -> refus
        # propre (pas de crash TypeError/PermissionError).
        print("[Mmd] Verrou .running.lock deja pris par un autre process "
              "(instance en cours ou residuelle). Arret. Tuez le process "
              "mmd_gui residuel, puis relancez.")
        return
    _write_meta(meta_path)  # metadonnees initiales (+ instance_id)
    threading.Thread(target=_heartbeat_loop, args=(meta_path,), daemon=True).start()

    api = Api()
    # Cold-start: assure le schema (CREATE TABLE IF NOT EXISTS) avant que
    # l'UI ne lise quoi que ce soit. Sans ca, 1er lancement crash (tables
    # lues avant leur 1re creation paresseuse), 2e lancement OK (tables
    # deja la). migrate() est idempotent et ne casse rien si deja present.
    try:
        migrations.migrate()
    except Exception:
        pass
    # user-data-dir unique par PID pour eviter le conflit de dossier pywebview
    # (le msg 'Failed to delete user data folder' vient de 2 instances qui se
    # marchent dessus sur le meme dossier). On force WebView2 (edgechromium).
    # FIX pywebview v6 crash (blackscreen + pool de handles sature):
    # private_mode=True (defaut) cree un dossier temp WebView2 (EBWebView)
    # NEUF a chaque session -> apres N crashes le pool de handles est sature.
    # On force private_mode=False + un dossier user-data FIXE via la variable
    # d'environnement native WebView2 (WEBVIEW2_USER_DATA_FOLDER). Ainsi le
    # meme dossier .webview_data est reutilise a chaque lancement (plus de
    # temp orphelin par session). NB: le kwarg 'storage_path' n'existe PAS en
    # pywebview 6.2.1 (ajoute dans une version ulterieure) -> on utilise
    # l'env var WebView2 a la place.
    webview_dir = os.path.join(HERE, ".webview_data")
    os.makedirs(webview_dir, exist_ok=True)
    # Fix dossier user-data fixe via la variable d'environnement native WebView2.
    # pywebview 6.2.1 N'accepte NI 'storage_path' NI 'private_mode' en kwarg de
    # create_window (TypeError au demarrage) -> on utilise WEBVIEW2_USER_DATA_FOLDER.
    os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", webview_dir)
    def _setup_hwnd():
        try:
            hwnd = _get_win_hwnd()
            if hwnd:
                import ctypes
                GWL_STYLE = -16
                WS_THICKFRAME = 0x00040000
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
                # frameless=True a déjà enlevé WS_CAPTION. On remet uniquement WS_THICKFRAME pour le resize/snap natif.
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
                ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004)
        except Exception:
            pass

    win_kwargs = dict(
        js_api=api, width=1600, height=1000,
        min_size=(1100, 700), background_color="#05070d", text_select=False,
        frameless=True, easy_drag=False,
    )
    try:
        # tente edgechromium (WebView2) en premier — plus stable sous Windows
        WIN = webview.create_window("EVE Market Manager", INDEX, **win_kwargs)
    except Exception:
        WIN = webview.create_window("EVE Market Manager", INDEX, **win_kwargs)

    _hotkeys_started = threading.Event()
    # Cache cote JS ; hotkeys seulement lorsque les fonctions JS sont chargees.
    def _boot_render():
        _setup_hwnd()
        if not _hotkeys_started.is_set():
            _hotkeys_started.set()
            _start_global_hotkeys()
    try:
        webview.windows[0].events.loaded += _boot_render
    except Exception:
        pass

    def _start_global_hotkeys():
        """Ecouteur global de raccourcis clavier Windows (System-Wide RegisterHotKey).
        Alt+Shift+F (Suivant) et Ctrl+Shift+F (Precedent) fonctionnent en arriere-plan
        meme lorsque le client EVE Online ou n'importe quelle autre fenetre est active !"""
        def hotkey_loop():
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32

            MOD_ALT_SHIFT = 0x0001 | 0x0004
            MOD_CTRL_SHIFT = 0x0002 | 0x0004
            VK_F = 0x46

            registered = []
            for hotkey_id, modifiers in (
                    (101, MOD_ALT_SHIFT), (102, MOD_CTRL_SHIFT)):
                if user32.RegisterHotKey(None, hotkey_id, modifiers, VK_F):
                    registered.append(hotkey_id)
            if not registered:
                return
            try:
                msg = wintypes.MSG()
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == 0x0312:  # WM_HOTKEY
                        direction = {101: 1, 102: -1}.get(int(msg.wParam))
                        if direction:
                            _dispatch_navigation(WIN, direction)
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            finally:
                for hotkey_id in registered:
                    user32.UnregisterHotKey(None, hotkey_id)

        t = threading.Thread(target=hotkey_loop, daemon=True)
        t.start()


    # watcher auto du dossier Marketlogs (comme Mmd): export EVE -> maj auto
    try:
        import mmd_watch
        mmd_watch.start_watcher(api)
    except Exception:
        pass
    try:
        # force le moteur WebView2 si disponible (evite le fallback qui crash)
        webview.start(gui="edgechromium", debug=False)
    except Exception:
        webview.start(None, debug=False)
    finally:
        # libere le verrou a la fermeture
        try:
            if 'lock_fd' in dir() and lock_fd:
                import msvcrt
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
                lock_fd.close()
        except Exception:
            pass
        try:
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except Exception:
            pass
        try:
            if os.path.exists(meta_path):
                os.remove(meta_path)
        except Exception:
            pass


if __name__ == "__main__":
    main()
