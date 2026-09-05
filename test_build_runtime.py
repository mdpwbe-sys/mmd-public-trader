"""Small source-level smoke test for dynamic modules required by onefile builds."""
import importlib
from pathlib import Path
import unittest


class BuildRuntimeTests(unittest.TestCase):
    def test_local_analyzer_is_importable_and_declared_for_pyinstaller(self):
        module = importlib.import_module("eve_local_analyzer")
        self.assertTrue(hasattr(module, "LocalAnalyzer"))
        build_script = (Path(__file__).parent / "build_exe.py").read_text(encoding="utf-8")
        self.assertIn('"--hidden-import=eve_local_analyzer"', build_script)
        self.assertIn('"--noupx"', build_script)
        self.assertIn('"--version-file={version_file}"', build_script)
        self.assertIn('parser.add_argument("--onedir"', build_script)

    def test_version_is_centralized_for_windows_metadata(self):
        version = importlib.import_module("version")
        self.assertEqual(version.VERSION, "0.1.7")
        self.assertEqual(importlib.import_module("__version__").VERSION, version.VERSION)


if __name__ == "__main__":
    unittest.main()
