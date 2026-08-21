#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mmd_tick.py - prix EVE en Decimal exact (JAMAIS float).

Regle CCP: 4 chiffres significatifs max.
  tick(p) = 10^(floor(log10(p)) - 3), planche a 0.01.
  Autour de 1M: ticks valides = 999900, 1000000, 1001000, 1002000 (PAS 999999).

Prix internes stockes en centiemes d'ISK (entier) pour eviter toute derive
a virgule flottante. Decimal utilise pour tous les calculs de tick.

Fonctions testees (voir __main__):
  normalize_price(p)      -> prix valide le plus proche (arrondi au tick)
  previous_valid_tick(p)  -> tick valide strictement inferieur
  next_valid_tick(p)      -> tick valide strictement superieur
  is_valid_price(p)       -> True si p tombe exactement sur un tick valide
"""
from decimal import Decimal, ROUND_HALF_UP, getcontext, ROUND_FLOOR, ROUND_CEILING

getcontext().prec = 28

# representation interne: centiemes d'ISK (entier)
# 1 ISK = 100 centiemes. Tout prix EVE est un multiple de 0.01 ISK.
CENTS_PER_ISK = 100

TICK_FLOOR = Decimal("0.01")


def _as_dec(p):
    """Convertit float/str/int/Decimal en Decimal. Rejette les pourris."""
    if isinstance(p, Decimal):
        return p
    if isinstance(p, (int, str)):
        return Decimal(str(p))
    if isinstance(p, float):
        # float en entree: on passe par str pour ne pas trainer d'erreur binaire
        return Decimal(str(p))
    raise TypeError(f"prix non supporte: {type(p)}")


def _to_cents(p):
    """Prix -> entier centiemes d'ISK (Decimal => int sans derive)."""
    d = _as_dec(p)
    # arrondi a 2 dec d'ISK (centieme) car CCP ne permet pas sous-centime
    d2 = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int((d2 * CENTS_PER_ISK).to_integral_value(rounding=ROUND_HALF_UP))


def _from_cents(c):
    """Entier centiemes -> Decimal ISK exact."""
    return Decimal(c) / CENTS_PER_ISK


def tick_size(p):
    """Taille de tick valide pour un prix p (Decimal)."""
    import math
    d = _as_dec(p)
    if d <= 0:
        return TICK_FLOOR
    # floor(log10(p)) - 3
    exp = int(math.floor(d.log10())) - 3
    t = Decimal(10) ** exp
    if t < TICK_FLOOR:
        return TICK_FLOOR
    return t


def is_valid_price(p):
    """True si p tombe exactement sur un tick valide (multiple exact du tick)."""
    d = _as_dec(p)
    if d <= 0:
        return False
    t = tick_size(d)
    # p / t doit etre un entier exact
    q = (d / t)
    return q == q.to_integral_value(rounding=ROUND_FLOOR)


def normalize_price(p):
    """Prix affiche/calcule -> prix valide CCP le plus proche (au tick).
    Au milieu d'un tick, arrondi par defaut (ROUND_FLOOR) = standard CCP."""
    d = _as_dec(p)
    if d <= 0:
        return TICK_FLOOR
    t = tick_size(d)
    # arrondi au tick inferieur (defaut CCP)
    n = (d / t).to_integral_value(rounding=ROUND_FLOOR)
    norm = n * t
    # renormalisation: le tick peut changer d'ordre de grandeur si on franchit
    # un seuil (ex: 999900 -> 1000000). On reverifie le tick du resultat.
    t2 = tick_size(norm)
    if t2 != t:
        n2 = (norm / t2).to_integral_value(rounding=ROUND_FLOOR)
        norm = n2 * t2
    return norm


def previous_valid_tick(p):
    """Tick valide strictement inferieur a p."""
    d = _as_dec(p)
    if d <= TICK_FLOOR:
        return TICK_FLOOR
    t = tick_size(d)
    n = (d / t).to_integral_value(rounding=ROUND_FLOOR)
    prev = (n - 1) * t
    if prev < TICK_FLOOR:
        return TICK_FLOOR
    # renormalisation si franchit un seuil d'ordre de grandeur
    t2 = tick_size(prev)
    if t2 != t:
        n2 = ((prev / t2).to_integral_value(rounding=ROUND_FLOOR))
        prev = (n2 - 1) * t2 if n2 > 1 else TICK_FLOOR
    return prev if prev > 0 else TICK_FLOOR


def next_valid_tick(p):
    """Tick valide strictement superieur a p."""
    d = _as_dec(p)
    if d <= 0:
        return TICK_FLOOR
    t = tick_size(d)
    n = (d / t).to_integral_value(rounding=ROUND_CEILING)
    nxt = (n + 1) * t
    # renormalisation si franchit un seuil d'ordre de grandeur
    t2 = tick_size(nxt)
    if t2 != t:
        n2 = ((nxt / t2).to_integral_value(rounding=ROUND_CEILING))
        nxt = (n2 + 1) * t2
    return nxt


# --- compat avec l'existant: le moteur de scan attendait next_price(best, side) ---
def next_price(best_public, side, tick=None):
    """Nouveau prix a 1 tick du meilleur public.
    side=0 (BUY): +1 tick (monter). side=1 (SELL): -1 tick (descendre)."""
    bp = _as_dec(best_public)
    if bp <= 0:
        return None
    if side == 0:
        return next_valid_tick(bp)
    else:
        return previous_valid_tick(bp)


if __name__ == "__main__":
    tests = [
        ("999900 valide", is_valid_price(Decimal("999900")), True),
        ("999999 invalide", is_valid_price(Decimal("999999")), False),
        ("1000000 valide", is_valid_price(Decimal("1000000")), True),
        ("1001000 valide", is_valid_price(Decimal("1001000")), True),
        ("1000500 invalide", is_valid_price(Decimal("1000500")), False),
        ("0.05 valide", is_valid_price(Decimal("0.05")), True),
        ("0.051 invalide", is_valid_price(Decimal("0.051")), False),
        ("1234.56 invalide", is_valid_price(Decimal("1234.56")), False),
        ("1234 valide", is_valid_price(Decimal("1234")), True),
    ]
    ok = True
    for name, got, exp in tests:
        status = "OK" if got == exp else "FAIL"
        if got != exp: ok = False
        print(f"  [{status}] {name}: got={got} exp={exp}")
    # normalize
    norm_tests = [
        (Decimal("999999"), Decimal("999900")),
        (Decimal("999950"), Decimal("999900")),
        (Decimal("1000001"), Decimal("1000000")),
        (Decimal("1234.56"), Decimal("1234")),
        (Decimal("0.051"), Decimal("0.05")),
    ]
    for p, exp in norm_tests:
        got = normalize_price(p)
        status = "OK" if got == exp else "FAIL"
        if got != exp: ok = False
        print(f"  [{status}] normalize({p}) = {got} (exp {exp})")
    # next/prev
    np_tests = [
        (Decimal("999900"), next_valid_tick(Decimal("999900")), Decimal("1001000")),
        (Decimal("999900"), previous_valid_tick(Decimal("999900")), Decimal("999800")),
        (Decimal("0.05"), next_valid_tick(Decimal("0.05")), Decimal("0.06")),
    ]
    for p, got, exp in np_tests:
        status = "OK" if got == exp else "FAIL"
        if got != exp: ok = False
        print(f"  [{status}] next/prev from {p}: got={got} exp={exp}")
    print("TOUS TESTS:", "PASS" if ok else "FAIL")
