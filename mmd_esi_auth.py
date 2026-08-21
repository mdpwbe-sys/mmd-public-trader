#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transport ESI authentifie strictement read-only.

Les seules requetes POST autorisees sont les routes ``assets/names`` : elles
lisent les noms d'items et ne modifient aucun etat EVE. Les URLs sont construites
depuis un chemin allowliste; aucun appelant ne peut fournir un hote arbitraire.
"""
from dataclasses import dataclass, field
from decimal import Decimal
import email.utils
import json
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import mmd_esi as esi
import mmd_ratelimit as rl
import mmd_sso as sso

ESI_ROOT = "https://esi.evetech.net"
_ID = r"[1-9][0-9]*"
_GET_PATTERNS = tuple(re.compile(p) for p in (
    rf"/(?:latest|v5)/characters/{_ID}/",
    rf"/v4/(?:characters|corporations)/{_ID}/assets/",
    rf"/v1/corporations/{_ID}/divisions/",
    rf"/v1/corporations/{_ID}/wallets/",
    rf"/v2/corporations/{_ID}/orders/",
    rf"/v1/characters/{_ID}/wallet/transactions/",
    rf"/v1/corporations/{_ID}/wallets/[1-7]/transactions/",
    rf"/v1/(?:characters|corporations)/{_ID}/contracts/",
    rf"/v1/(?:characters|corporations)/{_ID}/contracts/{_ID}/items/",
))
_POST_PATTERNS = tuple(re.compile(p) for p in (
    rf"/v1/(?:characters|corporations)/{_ID}/assets/names/",
))
_cache = {}
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class EsiError:
    kind: str
    path: str
    message: str
    status: int | None = None
    retry_after: float | None = None


@dataclass(frozen=True)
class EsiResult:
    data: object = None
    headers: dict = field(default_factory=dict)
    error: EsiError | None = None
    status: int | None = None
    from_cache: bool = False

    @property
    def ok(self):
        return self.error is None


def failure(path, kind, message, status=None, retry_after=None):
    return EsiResult(error=EsiError(
        kind, path, message, status=status, retry_after=retry_after),
        status=status)


def _allowed(method, path):
    patterns = _GET_PATTERNS if method == "GET" else _POST_PATTERNS
    return method in ("GET", "POST") and any(p.fullmatch(path) for p in patterns)


def _header(headers, name):
    wanted = name.lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == wanted:
            return value
    return None


def _expires_at(headers):
    raw = _header(headers, "Expires")
    if not raw:
        return 0.0
    try:
        return email.utils.parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _observe(group, headers):
    rl.observe(group, headers)
    try:
        remaining = int(_header(headers, "X-Ratelimit-Remaining"))
        limit = int(str(_header(headers, "X-Ratelimit-Limit")).split("/")[0])
    except (TypeError, ValueError):
        return
    rl.throttle(group, remaining, limit)


def _retry_after(headers):
    try:
        return max(0.0, float(_header(headers, "Retry-After") or 5))
    except (TypeError, ValueError):
        return 5.0


def request_json(method, path, char_id, *, params=None, body=None,
                 timeout=20, max_attempts=5):
    """Execute une requete allowlistee et retourne toujours ``EsiResult``."""
    method = str(method).upper()
    if not _allowed(method, path):
        return failure(path, "not_allowed", "Route ESI non autorisee")
    try:
        cid = int(char_id)
        if cid <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return failure(path, "invalid_character", "Character ID invalide")
    query = urllib.parse.urlencode(sorted((params or {}).items()), doseq=True)
    url = ESI_ROOT + path + (("?" + query) if query else "")
    payload = None
    if body is not None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    cache_key = (cid, method, url, payload or b"")
    with _cache_lock:
        cached = _cache.get(cache_key)
    if method == "GET" and cached and cached["expires_at"] > time.time():
        return EsiResult(cached["data"], dict(cached["headers"]),
                         status=200, from_cache=True)
    token = sso._access_token(cid)
    if not token:
        return failure(path, "authentication", "Token ESI absent ou expire", 401)
    attempts = max(1, min(int(max_attempts), 6))
    refreshed = False
    group = rl._group_key(cid)
    for attempt in range(attempts):
        headers = {"Authorization": f"Bearer {token}", **esi.UA,
                   **esi.COMPAT_HEADER, "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if method == "GET" and cached and cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        req = urllib.request.Request(
            url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                response_headers = dict(response.headers.items())
                _observe(group, response_headers)
                data = json.loads(
                    response.read().decode("utf-8"), parse_float=Decimal)
                if method == "GET":
                    cached = {"data": data, "headers": response_headers,
                              "etag": _header(response_headers, "ETag"),
                              "expires_at": _expires_at(response_headers)}
                    with _cache_lock:
                        _cache[cache_key] = cached
                return EsiResult(data, response_headers, status=response.status)
        except urllib.error.HTTPError as exc:
            error_headers = dict(exc.headers.items()) if exc.headers else {}
            _observe(group, error_headers)
            if exc.code == 304 and cached:
                merged = dict(cached["headers"])
                merged.update(error_headers)
                return EsiResult(cached["data"], merged, status=304,
                                 from_cache=True)
            if exc.code == 401 and not refreshed:
                refreshed = True
                if sso._refresh(cid):
                    token = sso._access_token(cid)
                    if token:
                        continue
            if exc.code == 429:
                delay = _retry_after(error_headers)
                if attempt + 1 < attempts and delay <= 60:
                    time.sleep(delay)
                    continue
                return failure(path, "rate_limited", "Quota ESI atteint",
                               429, delay)
            if exc.code == 420 or exc.code in (500, 502, 503, 504):
                if attempt + 1 < attempts:
                    time.sleep(min(2 ** attempt, 16) + random.uniform(0, 0.5))
                    continue
                kind = "error_limit" if exc.code == 420 else "unavailable"
                return failure(path, kind, "ESI temporairement indisponible", exc.code)
            kind = {401: "authentication", 403: "forbidden",
                    404: "not_found"}.get(exc.code, "http")
            return failure(path, kind, f"Reponse ESI HTTP {exc.code}", exc.code)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 16) + random.uniform(0, 0.5))
                continue
            return failure(path, "network", "ESI inaccessible")
    return failure(path, "unavailable", "ESI indisponible")


def get_json(path, char_id, *, params=None):
    return request_json("GET", path, char_id, params=params)


def post_asset_names(path, char_id, item_ids):
    try:
        ids = list(dict.fromkeys(int(item_id) for item_id in item_ids))
    except (TypeError, ValueError):
        return failure(path, "invalid_body", "Liste d'item IDs invalide")
    if not 1 <= len(ids) <= 1000 or any(item_id <= 0 for item_id in ids):
        return failure(path, "invalid_body", "Entre 1 et 1000 item IDs requis")
    return request_json("POST", path, char_id, body=ids)


def fetch_all_pages(path, char_id, *, params=None, max_pages=1000):
    """Collecte X-Pages sans jamais retourner un snapshot partiel."""
    base = dict(params or {})
    base["page"] = 1
    first = get_json(path, char_id, params=base)
    if not first.ok:
        return first
    if not isinstance(first.data, list):
        return failure(path, "invalid_payload", "Liste ESI attendue")
    try:
        pages = int(_header(first.headers, "X-Pages") or 1)
    except (TypeError, ValueError):
        return failure(path, "invalid_pages", "X-Pages ESI invalide")
    if not 1 <= pages <= int(max_pages):
        return failure(path, "invalid_pages", "Nombre de pages ESI hors limite")
    rows = list(first.data)
    for page in range(2, pages + 1):
        query = dict(base)
        query["page"] = page
        result = get_json(path, char_id, params=query)
        if not result.ok:
            return result
        if not isinstance(result.data, list):
            return failure(path, "invalid_payload", f"Page {page} invalide")
        rows.extend(result.data)
    headers = dict(first.headers)
    headers["X-Collected-Pages"] = str(pages)
    return EsiResult(rows, headers, status=200,
                     from_cache=first.from_cache)


def clear_memory_cache():
    with _cache_lock:
        _cache.clear()
