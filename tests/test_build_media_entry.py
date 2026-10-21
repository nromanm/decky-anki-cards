"""
Run with: python3 tests/test_build_media_entry.py

Tests _build_media_entry, which builds the AnkiConnect picture/audio entry for add_note. Covers
the fix from this session: Anki's flatpak sandbox has no filesystem access outside its own app
data (confirmed via `flatpak info --show-permissions net.ankiweb.Anki`), so handing it a host
`path` (Steam's screenshot dir, our own /tmp audio extraction) always failed with "No such file"
from Anki's side regardless of whether the file genuinely existed on the host — and AnkiConnect
wrote that raw error into the note field instead of failing cleanly. Fix: read the file ourselves
and send base64 `data` instead of `path`, so Anki's sandbox never needs to see the file at all.
"""
import os
import sys
import types
import base64
import tempfile
import unittest

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


class BuildMediaEntryTest(unittest.TestCase):
    def test_empty_value_returns_none(self):
        self.assertIsNone(main._build_media_entry("", "Image"))
        self.assertIsNone(main._build_media_entry("   ", "Image"))

    def test_url_value_uses_url_not_path_or_data(self):
        entry = main._build_media_entry("https://example.com/pic.jpg", "Image")
        self.assertEqual(entry["url"], "https://example.com/pic.jpg")
        self.assertEqual(entry["filename"], "pic.jpg")
        self.assertNotIn("path", entry)
        self.assertNotIn("data", entry)

    def test_local_file_sends_base64_data_never_a_path(self):
        fd, path = tempfile.mkstemp(suffix=".jpg")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(b"fake image bytes")
            entry = main._build_media_entry(path, "Image")
        finally:
            os.remove(path)

        self.assertNotIn("path", entry)  # the actual bug: Anki's sandbox can't read a host path
        self.assertEqual(base64.b64decode(entry["data"]), b"fake image bytes")
        self.assertEqual(entry["filename"], os.path.basename(path))
        self.assertEqual(entry["fields"], ["Image"])

    def test_missing_local_file_is_skipped_not_sent_as_a_broken_path(self):
        self.assertIsNone(main._build_media_entry("/tmp/does_not_exist_xyz123.jpg", "Image"))


if __name__ == "__main__":
    unittest.main()
