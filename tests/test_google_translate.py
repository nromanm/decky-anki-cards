"""
Run with: python3 tests/test_google_translate.py

Tests _google_translate's request construction and response parsing against a mocked
urllib.request.urlopen (no real network/Google call). Covers the two bugs this plugin actually
hit on a real Deck: wrong query params for the unofficial endpoint, and the SSL context not
pointing at the system CA bundle (Decky Loader's frozen Python can't find it on its own, causing
CERTIFICATE_VERIFY_FAILED).
"""
import os
import sys
import types
import tempfile
import unittest
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


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class GoogleTranslateTest(unittest.TestCase):
    def test_sends_expected_query_params_and_parses_first_result(self):
        captured = {}

        def fake_urlopen(req, timeout=None, context=None):
            captured["url"] = req.full_url
            return _FakeResponse(b'["\\u00a1hola!"]')

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = main._google_translate("hi", "en", "es")

        self.assertEqual(result, "¡hola!")
        self.assertIn("client=dict-chrome-ex", captured["url"])
        self.assertIn("sl=en", captured["url"])
        self.assertIn("tl=es", captured["url"])
        self.assertIn("q=hi", captured["url"])

    def test_uses_system_ca_bundle_when_present(self):
        fd, ca_path = tempfile.mkstemp(suffix=".pem")
        os.close(fd)

        try:
            with mock.patch.object(main, "_SYSTEM_CA_BUNDLE", ca_path), \
                 mock.patch("ssl.create_default_context") as fake_create_context, \
                 mock.patch("urllib.request.urlopen", return_value=_FakeResponse(b'["ok"]')):
                main._google_translate("hi", "en", "es")
        finally:
            os.remove(ca_path)

        fake_create_context.assert_called_once_with(cafile=ca_path)

    def test_falls_back_to_default_context_when_ca_bundle_missing(self):
        captured = {}

        def fake_urlopen(req, timeout=None, context=None):
            captured["context"] = context
            return _FakeResponse(b'["ok"]')

        with mock.patch.object(main, "_SYSTEM_CA_BUNDLE", "/nonexistent/ca-bundle.pem"), \
             mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            main._google_translate("hi", "en", "es")

        self.assertIsNone(captured["context"])


if __name__ == "__main__":
    unittest.main()
