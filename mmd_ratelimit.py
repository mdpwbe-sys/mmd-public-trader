#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mmd_ratelimit.py - respect des bonnes pratiques CCP ESI (2026).

Deux familles de headers MUTUELLEMENT EXCLUSIVES selon la reponse:
  - Buckets modernes: X-Ratelimit-Limit / Remaining / Reset / Group
      * budget par groupe (applicationID, characterID); IP source pour le public
      * fenetre/budget variables -> on lit TOUJOURS X-Ratelimit-Limit
        (JAMAIS 15 min hardcode)
  - Ancienne error-limit globale: X-ESI-Error-Limit-Remain / -Reset
      * 100 reponses non-2xx/3xx par minute -> 420

Couts de tokens (nouveau systeme):
  2XX=2, 3XX(ETag/304)=1, 4XX=5 (sauf 429), 5XX=0.

On n'impose AUCUNE duree en dur: on reagit aux headers reels.
"""
import time
import threading

# etat par groupe (cle = 'char:ID' ou 'public')
_state = {}
_lock = threading.Lock()


def _group_key(char_id):
    return f"char:{char_id}" if char_id else "public"


def observe(group, headers):
    """Lit les headers de quota (les deux familles) et stocke l'etat.
    headers = dict-like (urllib response headers)."""
    if headers is None:
        return
    with _lock:
        s = _state.setdefault(group, {})
        # famille bucket moderne
        rem = headers.get("X-Ratelimit-Remaining")
        lim = headers.get("X-Ratelimit-Limit")
        grp = headers.get("X-Ratelimit-Group")
        if rem is not None:
            try: s["remaining"] = int(rem)
            except ValueError: pass
        if lim is not None:
            try: s["limit"] = int(lim.split("/")[0])
            except (ValueError, AttributeError): pass
        if grp is not None:
            s["group"] = grp
        # ancienne error-limit globale (mutuellement exclusive)
        elr = headers.get("X-ESI-Error-Limit-Remain")
        elres = headers.get("X-ESI-Error-Limit-Reset")
        if elr is not None:
            try: s["err_remain"] = int(elr)
            except ValueError: pass
        if elres is not None:
            try: s["err_reset"] = int(elres)
            except ValueError: pass


def throttle(group, remaining=None, limit=None):
    """Pause preventive si le bucket moderne est bas (lit les headers reels)."""
    if remaining is not None and limit is not None and limit > 0:
        if remaining <= max(2, limit * 0.05):
            time.sleep(0.4)


def error_limit_low(group):
    """True si l'ancienne error-limit globale est proche de 0 (risque de ban)."""
    with _lock:
        s = _state.get(group, {})
        rem = s.get("err_remain")
        return rem is not None and rem <= 5


def wait_retry_after(seconds):
    """Attend le Retry-After (plafonne a 30s pour ne pas bloquer l'UI)."""
    sec = max(1, min(int(float(seconds)), 30))
    time.sleep(sec)


def backoff(attempt):
    """Backoff exponentiel + jitter pour 420/5xx (plafonne 16s)."""
    time.sleep(min(2 ** attempt, 16))
