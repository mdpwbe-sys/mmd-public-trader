import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from eve_local_analyzer import LocalAnalyzer, LocalClipboardWatcher


class LocalPipelineTests(unittest.TestCase):
    def test_network_failure_is_visible_and_same_list_can_retry(self):
        with tempfile.TemporaryDirectory() as root:
            resolver = Mock(side_effect=[OSError("offline"), {"alpha pilot": 1, "beta pilot": 2}])
            analyzer = LocalAnalyzer(Path(root) / "cache.json", resolve_ids=resolver, fetch_stats=lambda _: {}, resolve_names=lambda _: {})
            updates = []
            watcher = LocalClipboardWatcher(analyzer, updates.append, sequence=iter([1, 2]).__next__, read_text=lambda: "Alpha Pilot\nBeta Pilot")
            self.assertFalse(watcher.poll_once())
            self.assertEqual(updates[-1]["state"], "error")
            self.assertTrue(watcher.poll_once())
            self.assertEqual(updates[-1]["state"], "ready")

    def test_manual_and_watcher_share_the_same_pipeline(self):
        with tempfile.TemporaryDirectory() as root:
            analyzer = LocalAnalyzer(Path(root) / "cache.json", resolve_ids=lambda _: {"alpha pilot": 1, "beta pilot": 2}, fetch_stats=lambda _: {}, resolve_names=lambda _: {})
            updates = []
            watcher = LocalClipboardWatcher(analyzer, updates.append, sequence=lambda: 1, read_text=lambda: "Alpha Pilot\nBeta Pilot")
            self.assertTrue(watcher.poll_once())
            self.assertTrue(watcher.analyze_now())
            self.assertEqual(updates[-1]["total"], 2)

if __name__ == "__main__": unittest.main()
