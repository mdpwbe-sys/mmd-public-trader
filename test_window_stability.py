"""Invariants Win32 du drag et du topmost des popups."""
import inspect
import unittest
from pathlib import Path
from unittest import mock

try:
    import mmd_gui as gui
except Exception:
    gui = None


@unittest.skipIf(gui is None, "pywebview absent de cet environnement")
class WindowStabilityTests(unittest.TestCase):
    def test_topmost_changes_only_z_order(self):
        class FakeUser32:
            def __init__(self):
                self.calls = []

            def GetAncestor(self, hwnd, mode):
                self.calls.append(("ancestor", hwnd, mode))
                return 456

            def SetWindowPos(self, *args):
                self.calls.append(("set",) + args)
                return 1

        user32 = FakeUser32()
        self.assertTrue(gui._apply_window_topmost(user32, 123, True))
        self.assertTrue(gui._apply_window_topmost(user32, 123, False))
        set_calls = [call for call in user32.calls if call[0] == "set"]
        self.assertEqual(set_calls, [
            ("set", 456, -1, 0, 0, 0, 0, 0x0013),
            ("set", 456, -2, 0, 0, 0, 0, 0x0013),
        ])

    def test_popup_alias_uses_safe_topmost_path(self):
        with mock.patch.object(
                gui, "set_window_topmost_global", return_value=True) as safe:
            self.assertTrue(gui.Api().set_topmost(True))
        safe.assert_called_once_with(True)

    def test_topmost_retries_after_native_failure(self):
        previous = gui.WINDOW_TOPMOST_STATE
        self.addCleanup(setattr, gui, "WINDOW_TOPMOST_STATE", previous)
        gui.WINDOW_TOPMOST_STATE = False
        with mock.patch.object(gui, "_get_win_hwnd", return_value=123), \
                mock.patch.object(gui, "_window_user32", return_value=object()), \
                mock.patch.object(
                    gui, "_apply_window_topmost",
                    side_effect=[False, True]) as native:
            self.assertFalse(gui.set_window_topmost_global(True))
            self.assertFalse(gui.WINDOW_TOPMOST_STATE)
            self.assertTrue(gui.set_window_topmost_global(True))
            self.assertTrue(gui.WINDOW_TOPMOST_STATE)
        self.assertEqual(native.call_count, 2)

    def test_only_native_titlebar_drag_is_exposed(self):
        main_source = inspect.getsource(gui.main)
        self.assertIn("frameless=True, easy_drag=False", main_source)
        self.assertFalse(hasattr(gui.Api, "move_window"))
        self.assertFalse(hasattr(gui.Api, "move_window_physical"))
        self.assertNotIn("WIN.move(", inspect.getsource(gui.Api))
        self.assertNotIn(
            "pywebview-drag-region",
            Path(gui.INDEX).read_text(encoding="utf-8"))

    def test_drag_checks_stop_and_mouse_button_before_moving(self):
        source = inspect.getsource(gui.Api.start_native_drag)
        stop_check = source.index("if _DRAG_STOP.is_set()")
        button_check = source.index("if not is_down")
        cursor_read = source.index("GetCursorPos(ctypes.byref(curr_pt))")
        move = source.index("user32.SetWindowPos", cursor_read)
        self.assertLess(stop_check, cursor_read)
        self.assertLess(button_check, cursor_read)
        self.assertLess(cursor_read, move)
        self.assertIn("start_win_x + last_dx", source)
        self.assertIn("start_win_y + last_dy", source)
        for zone in (
                "top-left", "top-right", "bottom-left", "bottom-right",
                "top", "left", "right"):
            self.assertIn(f'"{zone}"', source)

        gui._DRAG_STOP.clear()
        self.assertTrue(gui.Api().stop_native_drag())
        self.assertTrue(gui._DRAG_STOP.is_set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
