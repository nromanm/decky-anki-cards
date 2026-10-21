"""
Run with: python3 tests/test_dictionary_lookups.py

Tests _jisho_lookup, _wiktionary_lookup and _strip_html against a mocked
urllib.request.urlopen (no real network/Jisho/Wiktionary call) — the dictionary-lookup feature
added this session. Also covers lookup_dictionary's routing (Japanese -> Jisho, everything else
-> Wiktionary, unsupported language -> a clear error before any network call).
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


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class JishoLookupTest(unittest.TestCase):
    def test_formats_headword_reading_and_up_to_3_senses(self):
        body = (
            b'{"data": [{"japanese": [{"word": "\xe7\x8c\xab", "reading": "\xe3\x81\xad\xe3\x81\x93"}], '
            b'"senses": ['
            b'{"english_definitions": ["cat"], "parts_of_speech": ["Noun"]}, '
            b'{"english_definitions": ["shamisen"], "parts_of_speech": ["Noun"]}, '
            b'{"english_definitions": ["geisha"], "parts_of_speech": ["Noun"]}, '
            b'{"english_definitions": ["4th sense, dropped"], "parts_of_speech": ["Noun"]}'
            b']}]}'
        )
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            result = main._jisho_lookup("\u732b")
        self.assertEqual(
            result,
            "\u732b (\u306d\u3053)\n1. cat (Noun)\n2. shamisen (Noun)\n3. geisha (Noun)",
        )

    def test_no_entries_returns_empty_string(self):
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(b'{"data": []}')):
            self.assertEqual(main._jisho_lookup("asdkfjaslkdjf"), "")


class StripHtmlTest(unittest.TestCase):
    def test_strips_tags_and_unescapes_entities(self):
        html_text = 'A <a href="/wiki/cat">cat</a> &amp; a <i>dog</i>'
        self.assertEqual(main._strip_html(html_text), "A cat & a dog")


class WiktionaryLookupTest(unittest.TestCase):
    def test_formats_up_to_3_definitions_with_part_of_speech(self):
        body = (
            b'{"es": ['
            b'{"partOfSpeech": "noun", "definitions": ['
            b'{"definition": "<a href=\\"/wiki/house\\">house</a>"}'
            b']}, '
            b'{"partOfSpeech": "verb", "definitions": ['
            b'{"definition": "inflection of casar"}, '
            b'{"definition": "third-person singular present"}, '
            b'{"definition": "dropped, past the cap of 3"}'
            b']}'
            b']}'
        )
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            result = main._wiktionary_lookup("casa", "es")
        self.assertEqual(
            result,
            "casa\n1. house (noun)\n2. inflection of casar (verb)\n3. third-person singular present (verb)",
        )

    def test_language_not_present_returns_empty_string(self):
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(b'{"en": [{}]}')):
            self.assertEqual(main._wiktionary_lookup("word", "zh"), "")

    def test_404_returns_empty_string(self):
        err = main.urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertEqual(main._wiktionary_lookup("nonexistentword", "en"), "")

    def test_non_404_http_error_propagates(self):
        err = main.urllib.error.HTTPError("url", 500, "Server Error", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(main.urllib.error.HTTPError):
                main._wiktionary_lookup("word", "en")


class LookupDictionaryRoutingTest(unittest.TestCase):
    def test_japanese_routes_to_jisho(self):
        plugin = main.Plugin()
        with mock.patch.object(main, "_jisho_lookup", return_value="jisho result") as fake_jisho, \
             mock.patch.object(main, "_wiktionary_lookup") as fake_wiktionary:
            result = asyncio.run(plugin.lookup_dictionary("\u732b", "Japanese"))
        self.assertEqual(result, "jisho result")
        fake_jisho.assert_called_once_with("\u732b")
        fake_wiktionary.assert_not_called()

    def test_other_language_routes_to_wiktionary_with_its_code(self):
        plugin = main.Plugin()
        with mock.patch.object(main, "_jisho_lookup") as fake_jisho, \
             mock.patch.object(main, "_wiktionary_lookup", return_value="wiktionary result") as fake_wiktionary:
            result = asyncio.run(plugin.lookup_dictionary("casa", "Spanish"))
        self.assertEqual(result, "wiktionary result")
        fake_wiktionary.assert_called_once_with("casa", "es")
        fake_jisho.assert_not_called()

    def test_unsupported_language_raises_without_any_network_call(self):
        plugin = main.Plugin()
        with mock.patch.object(main, "_jisho_lookup") as fake_jisho, \
             mock.patch.object(main, "_wiktionary_lookup") as fake_wiktionary:
            with self.assertRaises(Exception) as ctx:
                asyncio.run(plugin.lookup_dictionary("word", "Klingon"))
        self.assertIn("Klingon", str(ctx.exception))
        fake_jisho.assert_not_called()
        fake_wiktionary.assert_not_called()


if __name__ == "__main__":
    unittest.main()
