#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mmd_price.py - prix EVE en Decimal exact (JAMAIS float).

Representation interne: tout prix est stocke en CENTIEMES d'ISK (entier).
  1 ISK = 100 centiemes. 28.01 ISK = 2801 centiemes.
  Aucune derive flottante: on ne touche jamais a un float pour un prix.

Regle CCP (4 chiffres significatifs max, donc le 4e chiffre est le dernier
modifiable; en dessous on ne peut changer que ce 4e chiffre):
  - jusqu'a 99,99     -> tick 0,01
  - a partir de 100,00 -> tick 0,1
  - a partir de 1000   -> tick 1
  - a partir de 10000  -> tick 10
  ... etc. (tick = 10^(floor(log10(p))-3), planche a 0,01)

Fonctions demandees (testees dans __main__):
  normalize_price(p)      -> prix valide CCP le plus proche (arrondi au tick)
  previous_valid_tick(p)  -> tick valide strictement inferieur
  next_valid_tick(p)      -> tick valide strictement superieur
  is_valid_price(p)       -> True si p tombe exactement sur un tick valide

Helpers cents (partages import/core/JS):
  to_cents(p)    -> int (centiemes) depuis float/str/int/Decimal
  from_cents(c)  -> Decimal ISK exact
  fmt_cents(c)   -> "1.000.000,00" (format EVE, pour JS ou affichage Python)
  ticks_exact(p) -> True si p est un multiple exact du tick courant
"""
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING, getcontext
import math

getcontext().prec = 28

CENTS_PER_ISK = 100
TICK_FLOOR = Decimal("0.01")


def _as_dec(p):
    """float/str/int/Decimal -> Decimal (float via str pour eviter derive)."""
    if isinstance(p, Decimal):
        return p
    if isinstance(p, (int, str)):
        return Decimal(str(p))
    if isinstance(p, float):
        return Decimal(str(p))
    raise TypeError(f"prix non supporte: {type(p)}")


# ----------------------------------------------------------------------
# Representation en centiemes (entier) pour la DB/JSON/JS
# ----------------------------------------------------------------------
def to_cents(p):
    """Prix -> entier centiemes d'ISK (sans derive flottante)."""
    d = _as_dec(p)
    d2 = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int((d2 * CENTS_PER_ISK).to_integral_value(rounding=ROUND_HALF_UP))


def from_cents(c):
    """Entier centiemes -> Decimal ISK exact."""
    return Decimal(int(c)) / CENTS_PER_ISK


def fmt_cents(c):
    """Entier centiemes -> '1.000.000,00' (format EVE)."""
    d = from_cents(c)
    s = f"{d:,.2f}"
    # format fr (points milliers, virgule decimale)
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


# ----------------------------------------------------------------------
# Taille de tick (regle CCP 4 chiffres significatifs)
# ----------------------------------------------------------------------
def tick_size(p):
    """Taille de tick valide pour un prix p (Decimal)."""
    d = _as_dec(p)
    if d <= 0:
        return TICK_FLOOR
    exp = int(math.floor(d.log10())) - 3
    t = Decimal(10) ** exp
    if t < TICK_FLOOR:
        return TICK_FLOOR
    return t


def ticks_exact(p):
    """True si p est un multiple exact du tick courant (sans reste)."""
    d = _as_dec(p)
    if d <= 0:
        return False
    t = tick_size(d)
    q = d / t
    return q == q.to_integral_value(rounding=ROUND_FLOOR)


# ----------------------------------------------------------------------
# 4 fonctions demandees
# ----------------------------------------------------------------------
def is_valid_price(p):
    """True si p tombe exactement sur un tick valide CCP."""
    return ticks_exact(p)


def normalize_price(p):
    """Prix calcule/affiche -> prix valide CCP le plus proche (au tick)."""
    d = _as_dec(p)
    if d <= 0:
        return TICK_FLOOR
    t = tick_size(d)
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
    # Pour une valeur deja valide, reculer d'un tick. Pour une valeur situee
    # entre deux ticks, prendre directement le tick inferieur.
    n = (d / t).to_integral_value(rounding=ROUND_CEILING) - 1
    prev = n * t
    if prev < TICK_FLOOR:
        return TICK_FLOOR
    t2 = tick_size(prev)
    if t2 != t:
        # Le tick devient plus fin sous une puissance de dix. Repartir du
        # prix original evite de sauter les neuf ticks valides intermediaires.
        n2 = (d / t2).to_integral_value(rounding=ROUND_CEILING) - 1
        prev = n2 * t2
    return prev if prev > 0 else TICK_FLOOR


def next_valid_tick(p):
    """Tick valide strictement superieur a p."""
    d = _as_dec(p)
    if d <= 0:
        return TICK_FLOOR
    t = tick_size(d)
    n = (d / t).to_integral_value(rounding=ROUND_FLOOR)
    nxt = (n + 1) * t
    t2 = tick_size(nxt)
    if t2 != t:
        # Au changement de magnitude, le candidat est deja le premier prix
        # de la nouvelle grille : ne pas lui ajouter un second tick.
        n2 = (nxt / t2).to_integral_value(rounding=ROUND_FLOOR)
        nxt = n2 * t2
    return nxt


def next_price(best_public, side):
    """Nouveau prix a 1 tick du meilleur public.
    side=0 (BUY): +1 tick (monter). side=1 (SELL): -1 tick (descendre)."""
    bp = _as_dec(best_public)
    if bp <= 0:
        return None
    return next_valid_tick(bp) if side == 0 else previous_valid_tick(bp)


if __name__ == "__main__":
    ok = True
    def check(name, got, exp):
        global ok
        st = "OK" if got == exp else "FAIL"
        if got != exp: ok = False
        print(f"  [{st}] {name}: got={got} exp={exp}")

    # --- 4 fonctions demandees ---
    check("is_valid 999900", is_valid_price(Decimal("999900")), True)
    check("is_valid 999999", is_valid_price(Decimal("999999")), False)
    check("is_valid 1000000", is_valid_price(Decimal("1000000")), True)
    check("is_valid 1000500", is_valid_price(Decimal("1000500")), False)
    check("is_valid 0.05", is_valid_price(Decimal("0.05")), True)
    check("is_valid 0.051", is_valid_price(Decimal("0.051")), False)
    check("is_valid 1234.56", is_valid_price(Decimal("1234.56")), False)
    check("is_valid 1234", is_valid_price(Decimal("1234")), True)

    # --- regle: 0.01 jusqu'a 99.99, 0.1 a 100, 1 a 1000 ---
    check("tick 99.99 = 0.01", tick_size(Decimal("99.99")), Decimal("0.01"))
    check("tick 100.00 = 0.1", tick_size(Decimal("100")), Decimal("0.1"))
    check("tick 999 = 0.1", tick_size(Decimal("999")), Decimal("0.1"))
    check("tick 1000 = 1", tick_size(Decimal("1000")), Decimal("1"))
    check("tick 9999 = 1", tick_size(Decimal("9999")), Decimal("1"))
    check("tick 10000 = 10", tick_size(Decimal("10000")), Decimal("10"))

    # --- cents ---
    check("to_cents 28.01", to_cents(28.01), 2801)
    check("to_cents 0.02", to_cents(0.02), 2)
    check("to_cents 3696000", to_cents(3696000), 369600000)
    check("from_cents 2801", from_cents(2801), Decimal("28.01"))
    check("fmt_cents 2801", fmt_cents(2801), "28,01")
    check("fmt_cents 100000000", fmt_cents(100000000), "1.000.000,00")

    # --- normalize / next / prev ---
    check("normalize 999999", normalize_price(Decimal("999999")), Decimal("999900"))
    check("normalize 1000001", normalize_price(Decimal("1000001")), Decimal("1000000"))
    check("next 999900", next_valid_tick(Decimal("999900")), Decimal("1000000"))
    check("prev 1000000", previous_valid_tick(Decimal("1000000")), Decimal("999900"))
    check("prev 999900", previous_valid_tick(Decimal("999900")), Decimal("999800"))
    check("next 0.05", next_valid_tick(Decimal("0.05")), Decimal("0.06"))
    check("prev 0.05 (floor 0.01)", previous_valid_tick(Decimal("0.05")), Decimal("0.04"))

    print("TOUS TESTS:", "PASS" if ok else "FAIL")
