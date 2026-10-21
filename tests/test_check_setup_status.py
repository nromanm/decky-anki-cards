"""
Run with: python3 tests/test_check_setup_status.py

Tests check_setup_status's branching against a mocked _ankiconnect_request/subprocess.run (no
real Anki/AnkiConnect) — the readiness-check feature added this session (Anki installed, deck
exists, sync status, all folded into one call instead of several separate ones).
"""
import os
import sys
import types
import tempfile
import unittest
import asyncio
from unittest import mock

if "decky" not in sys.modules:
    fake_decky = types.ModuleType("decky")
    fake_decky.DECKY_USER_HOME = tempfile.gettempdir()

    class _FakeLogger:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    fake_decky.logger = _FakeLogger()
    sys.modules["decky"] = fake_decky

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main  # noqa: E402


def _fake_flatpak_result(returncode):
    result = mock.Mock()
    result.returncode = returncode
    return result


class CheckSetupStatusTest(unittest.TestCase):
    def test_ankiconnect_unreachable_stops_early_with_everything_else_false(self):
        plugin = main.Plugin()

        def fake_request(action, params=None, version=6):
            raise Exception("connection refused")

        with mock.patch("subprocess.run", return_value=_fake_flatpak_result(0)), \
             mock.patch.object(main, "_ankiconnect_request", side_effect=fake_request):
            status = asyncio.run(plugin.check_setup_status())

        self.assertEqual(status, {
            "anki_installed": True,
            "anki_connect_reachable": False,
            "deck_exists": False,
            "sync_ok": False,
            "sync_error": "",
        })

    def test_reachable_deck_missing_sync_fails_reports_the_sync_error(self):
        plugin = main.Plugin()

        def fake_request(action, params=None, version=6):
            if action == "version":
                return 6
            if action == "deckNames":
                return ["Default"]  # plugin's deck not among them
            if action == "sync":
                raise Exception("Collection lifecycle operation is active.")
            raise AssertionError(f"unexpected action {action!r}")

        with mock.patch("subprocess.run", return_value=_fake_flatpak_result(0)), \
             mock.patch.object(main, "_ankiconnect_request", side_effect=fake_request):
            status = asyncio.run(plugin.check_setup_status())

        self.assertEqual(status, {
            "anki_installed": True,
            "anki_connect_reachable": True,
            "deck_exists": False,
            "sync_ok": False,
            "sync_error": "Collection lifecycle operation is active.",
        })

    def test_everything_ready(self):
        plugin = main.Plugin()

        def fake_request(action, params=None, version=6):
            if action == "version":
                return 6
            if action == "deckNames":
                return [main.DECK_NAME]
            if action == "sync":
                return None
            raise AssertionError(f"unexpected action {action!r}")

        with mock.patch("subprocess.run", return_value=_fake_flatpak_result(0)), \
             mock.patch.object(main, "_ankiconnect_request", side_effect=fake_request):
            status = asyncio.run(plugin.check_setup_status())

        self.assertEqual(status, {
            "anki_installed": True,
            "anki_connect_reachable": True,
            "deck_exists": True,
            "sync_ok": True,
            "sync_error": "",
        })

    def test_anki_not_installed(self):
        plugin = main.Plugin()
        with mock.patch("subprocess.run", return_value=_fake_flatpak_result(1)), \
             mock.patch.object(main, "_ankiconnect_request", side_effect=Exception("unreachable")):
            status = asyncio.run(plugin.check_setup_status())
        self.assertFalse(status["anki_installed"])


if __name__ == "__main__":
    unittest.main()
