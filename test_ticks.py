#!/usr/bin/env python3
"""Tests de non-regression des ticks EVE aux changements de magnitude."""

import unittest
from decimal import Decimal

import mmd_price as price


class TickBoundaryTests(unittest.TestCase):
    ADJACENT_TICKS = (
        ("0.04", "0.05"),
        ("9.99", "10"),
        ("99.99", "100"),
        ("999.9", "1000"),
        ("9999", "10000"),
        ("99990", "100000"),
        ("999900", "1000000"),
    )

    def test_next_crosses_each_boundary_without_skipping(self):
        for lower, upper in self.ADJACENT_TICKS:
            with self.subTest(lower=lower, upper=upper):
                self.assertEqual(
                    price.next_valid_tick(Decimal(lower)), Decimal(upper))

    def test_previous_crosses_each_boundary_without_skipping(self):
        for lower, upper in self.ADJACENT_TICKS:
            with self.subTest(lower=lower, upper=upper):
                self.assertEqual(
                    price.previous_valid_tick(Decimal(upper)), Decimal(lower))

    def test_requested_reference_values(self):
        cases = (
            (price.next_valid_tick, "999900", "1000000"),
            (price.previous_valid_tick, "1000000", "999900"),
            (price.next_valid_tick, "99900", "99910"),
            (price.previous_valid_tick, "100000", "99990"),
            (price.next_valid_tick, "99.99", "100"),
            (price.previous_valid_tick, "100", "99.99"),
            (price.next_valid_tick, "9.99", "10"),
            (price.previous_valid_tick, "10", "9.99"),
            (price.next_valid_tick, "0.04", "0.05"),
            (price.previous_valid_tick, "0.05", "0.04"),
        )
        for function, value, expected in cases:
            with self.subTest(function=function.__name__, value=value):
                self.assertEqual(function(Decimal(value)), Decimal(expected))

    def test_invalid_prices_are_bracketed_by_nearest_ticks(self):
        cases = (
            ("0.051", "0.05", "0.06"),
            ("99.999", "99.99", "100"),
            ("99999", "99990", "100000"),
            ("999999", "999900", "1000000"),
        )
        for value, lower, upper in cases:
            with self.subTest(value=value):
                actual_lower = price.previous_valid_tick(Decimal(value))
                actual_upper = price.next_valid_tick(Decimal(value))
                self.assertEqual(actual_lower, Decimal(lower))
                self.assertEqual(actual_upper, Decimal(upper))
                self.assertTrue(price.is_valid_price(actual_lower))
                self.assertTrue(price.is_valid_price(actual_upper))

    def test_next_price_uses_correct_direction(self):
        self.assertEqual(price.next_price(Decimal("999900"), 0),
                         Decimal("1000000"))
        self.assertEqual(price.next_price(Decimal("1000000"), 1),
                         Decimal("999900"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
