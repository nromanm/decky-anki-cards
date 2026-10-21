"""
Run with: python3 tests/test_ensure_deck_and_model.py

Tests _ensure_deck_and_model's migration path for a note type that already exists (mocked
_ankiconnect_request, no real Anki) — covers the bug fixed this session: modelFieldAdd adds a
field's data slot but never touches the card template, so a note type created before
Example/Translation existed kept storing that data without ever displaying it (confirmed via
notesInfo: the fields had real text, but the "Card 1" Back template had no {{Example}}/
{{Translation}} placeholder at all). The fix pushes updateModelTemplates/updateModelStyling every
time an existing model is found, not just modelFieldAdd for missing fields.
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


class EnsureDeckAndModelTest(unittest.TestCase):
    def test_existing_model_gets_template_and_styling_pushed(self):
        plugin = main.Plugin()
        calls = []

        def fake_request(action, params=None, version=6):
            calls.append((action, params))
            if action == "modelNames":
                return [main.NOTE_TYPE_NAME]
            if action == "modelFieldNames":
                return list(main.NOTE_TYPE_FIELDS)  # nothing missing, this note type is current
            return None

        with mock.patch.object(main, "_ankiconnect_request", side_effect=fake_request):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(plugin._ensure_deck_and_model(loop, main.DECK_NAME))
            finally:
                loop.close()

        actions = [action for action, _ in calls]
        self.assertIn("updateModelTemplates", actions)
        self.assertIn("updateModelStyling", actions)

        template_call = next(params for action, params in calls if action == "updateModelTemplates")
        self.assertEqual(
            template_call["model"]["templates"]["Card 1"]["Back"],
            main.NOTE_TYPE_BACK_TEMPLATE,
        )
        self.assertIn("{{Example}}", template_call["model"]["templates"]["Card 1"]["Back"])
        self.assertIn("{{Translation}}", template_call["model"]["templates"]["Card 1"]["Back"])

        styling_call = next(params for action, params in calls if action == "updateModelStyling")
        self.assertEqual(styling_call["model"]["css"], main.NOTE_TYPE_CSS)

    def test_missing_fields_are_added_before_template_is_pushed(self):
        plugin = main.Plugin()
        calls = []

        def fake_request(action, params=None, version=6):
            calls.append((action, params))
            if action == "modelNames":
                return [main.NOTE_TYPE_NAME]
            if action == "modelFieldNames":
                # Simulates an old note type from before Example/Translation existed.
                return ["Morph", "Definition/Translation", "Image", "Audio"]
            return None

        with mock.patch.object(main, "_ankiconnect_request", side_effect=fake_request):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(plugin._ensure_deck_and_model(loop, main.DECK_NAME))
            finally:
                loop.close()

        field_add_calls = [params for action, params in calls if action == "modelFieldAdd"]
        added_field_names = {call["fieldName"] for call in field_add_calls}
        self.assertEqual(added_field_names, {"Example", "Translation"})
        # Template push must still happen even though fields also needed adding.
        self.assertIn("updateModelTemplates", [action for action, _ in calls])

    def test_brand_new_model_is_created_not_migrated(self):
        plugin = main.Plugin()
        calls = []

        def fake_request(action, params=None, version=6):
            calls.append((action, params))
            if action == "modelNames":
                return []  # note type doesn't exist yet
            return None

        with mock.patch.object(main, "_ankiconnect_request", side_effect=fake_request):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(plugin._ensure_deck_and_model(loop, main.DECK_NAME))
            finally:
                loop.close()

        actions = [action for action, _ in calls]
        self.assertIn("createModel", actions)
        self.assertNotIn("modelFieldAdd", actions)
        self.assertNotIn("updateModelTemplates", actions)  # createModel already has the current template


if __name__ == "__main__":
    unittest.main()
