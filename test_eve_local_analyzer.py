from pathlib import Path
import tempfile
import unittest

from eve_local_analyzer import (
    LocalAnalyzer,
    LocalClipboardWatcher,
    CF_OEMTEXT,
    CF_UNICODETEXT,
    _decode_clipboard_bytes,
    local_fingerprint,
    parse_local_names,
    profile_from_stats,
    risk_band,
)


class LocalAnalyzerTests(unittest.TestCase):
    def test_parse_local_names_deduplicates_a_multiline_eve_list(self):
        names = parse_local_names("Akim Otawabaru\nMike Craft\n  akim   otawabaru \n")
        self.assertEqual(names, ["Akim Otawabaru", "Mike Craft"])
        self.assertEqual(parse_local_names("un seul pilote"), [])
        self.assertEqual(local_fingerprint(names), local_fingerprint(["akim otawabaru", "mike craft"]))

    def test_profile_uses_zkill_danger_ratio_and_affiliation_ids(self):
        profile = profile_from_stats(42, "Pilot", {
            "dangerRatio": 78, "avgGangSize": 6.5, "soloRatio": 20,
            "shipsDestroyed": 10, "shipsLost": 2,
            "info": {"corporationID": 77, "alliance_id": 88},
        })
        self.assertEqual(profile["band"], "dangerous")
        self.assertEqual(profile["snuggly"], 22)
        self.assertEqual(profile["corporation_id"], 77)
        self.assertEqual(profile["alliance_id"], 88)
        self.assertEqual(risk_band(40), "watch")
        self.assertEqual(risk_band(39), "snuggly")

    def test_analyzer_streams_partial_result_and_reuses_fresh_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            calls, updates = [], []
            def stats(character_id):
                calls.append(character_id)
                return {"dangerRatio": 80 if character_id == 1 else 15, "info": {"corporation_id": 101}}
            analyzer = LocalAnalyzer(
                Path(directory) / "local.json", now=lambda: 1000,
                resolve_ids=lambda names: {"akim otawabaru": 1, "mike craft": 2},
                fetch_stats=stats, resolve_names=lambda ids: {101: "Example Corp"},
            )
            first = analyzer.analyze("Akim Otawabaru\nMike Craft", on_update=updates.append)
            self.assertEqual(calls, [1, 2])
            self.assertEqual(first["state"], "ready")
            self.assertEqual(first["dangerous"], 1)
            self.assertEqual(first["snuggly"], 1)
            self.assertEqual(first["pilots"][0]["name"], "Akim Otawabaru")
            self.assertEqual(first["pilots"][0]["corporation_name"], "Example Corp")
            self.assertEqual(updates[0]["state"], "loading")
            self.assertEqual(updates[-1]["state"], "ready")
            analyzer.analyze("Akim Otawabaru\nMike Craft")
            self.assertEqual(calls, [1, 2], "un cache frais ne réinterroge pas zKill")

    def test_known_kill_attacker_can_use_the_same_profile_cache_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            analyzer = LocalAnalyzer(
                Path(directory) / "local.json", now=lambda: 1000,
                fetch_stats=lambda _: {"dangerRatio": 72, "info": {}},
                resolve_names=lambda _: {},
            )
            result = analyzer.analyze_identities([(9001, "Hunter")])
            self.assertTrue(result["ok"])
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["pilots"][0]["character_id"], 9001)
            self.assertEqual(result["pilots"][0]["band"], "dangerous")

    def test_clipboard_watcher_ignores_same_list_and_only_handles_new_sequences(self):
        events, sequences = [], iter([1, 2, 3])
        class Analyzer:
            def analyze(self, text, on_update):
                events.append(text); on_update({"ok": True, "state": "ready"})
        watcher = LocalClipboardWatcher(Analyzer(), lambda _: None, sequence=lambda: next(sequences), read_text=lambda: "Akim Otawabaru\nMike Craft")
        self.assertTrue(watcher.poll_once())
        self.assertFalse(watcher.poll_once())
        self.assertFalse(watcher.poll_once())
        self.assertEqual(events, ["Akim Otawabaru\nMike Craft"])

    def test_clipboard_watcher_reports_an_unreadable_or_rejected_copy_without_logging_content(self):
        diagnostics = []
        watcher = LocalClipboardWatcher(
            object(), lambda _: None, sequence=iter([1, 2]).__next__,
            read_text=lambda: "", on_diagnostic=lambda event, count: diagnostics.append((event, count)),
        )
        self.assertFalse(watcher.poll_once())
        self.assertEqual(diagnostics, [("clipboard_empty", 0)])

    def test_clipboard_decodes_unicode_oem_and_registered_utf8_text(self):
        self.assertEqual(_decode_clipboard_bytes("Akim Otawabaru\0".encode("utf-16-le"), CF_UNICODETEXT), "Akim Otawabaru")
        self.assertEqual(_decode_clipboard_bytes(b"Mike Craft\0", CF_OEMTEXT), "Mike Craft")
        self.assertEqual(_decode_clipboard_bytes(b"Zylnarius\0", 0xC001, "UTF-8"), "Zylnarius")

    def test_clipboard_watcher_retries_a_transient_eve_clipboard_lock(self):
        events, diagnostics = [], []
        reads = iter(["", "Akim Otawabaru\nMike Craft"])
        class Analyzer:
            def analyze(self, text, on_update):
                events.append(text); on_update({"ok": True, "state": "ready"})
        watcher = LocalClipboardWatcher(
            Analyzer(), lambda _: None, sequence=lambda: 7, read_text=lambda: next(reads),
            on_diagnostic=lambda event, count: diagnostics.append((event, count)),
        )
        self.assertFalse(watcher.poll_once(), "le premier accès peut tomber pendant le verrou EVE")
        self.assertTrue(watcher.poll_once(), "la même séquence est relue après le verrou temporaire")
        self.assertEqual(events, ["Akim Otawabaru\nMike Craft"])
        self.assertEqual(diagnostics, [("clipboard_empty", 0), ("local_detected", 2)])

    def test_clipboard_watcher_survives_a_windows_read_error_and_recovers(self):
        diagnostics, events = [], []
        calls = {"count": 0}

        def sequence():
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("clipboard unavailable")
            return 42

        class Analyzer:
            def analyze(self, text, on_update):
                events.append(text)
                on_update({"ok": True, "state": "ready"})

        watcher = LocalClipboardWatcher(
            Analyzer(), lambda _: None, sequence=sequence,
            read_text=lambda: "Akim Otawabaru\nMike Craft",
            on_diagnostic=lambda event, count: diagnostics.append((event, count)),
        )
        self.assertFalse(watcher.poll_once())
        self.assertTrue(watcher.poll_once())
        self.assertEqual(diagnostics, [("clipboard_error", 0), ("local_detected", 2)])
        self.assertEqual(events, ["Akim Otawabaru\nMike Craft"])


if __name__ == "__main__":
    unittest.main()
