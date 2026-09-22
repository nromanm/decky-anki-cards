import os
import json
import urllib.request
import urllib.error
import urllib.parse

# The decky plugin module is located at decky-loader/plugin
# For easy intellisense checkout the decky-loader code repo
# and add the `decky-loader/plugin/imports` path to `python.analysis.extraPaths` in `.vscode/settings.json`
import decky
import asyncio

ANKICONNECT_URL = "http://127.0.0.1:8765"

NOTE_TYPE_NAME = "Decky Anki Plugin Note Type"
NOTE_TYPE_FIELDS = ["Morph", "Definition/Translation", "Image", "Audio"]
NOTE_TYPE_CSS = (
    ".card {\n"
    " font-family: arial;\n"
    " font-size: 20px;\n"
    " text-align: center;\n"
    " color: black;\n"
    " background-color: white;\n"
    "}"
)
NOTE_TYPE_FRONT_TEMPLATE = "{{Morph}}"
NOTE_TYPE_BACK_TEMPLATE = (
    "{{FrontSide}}\n\n<hr id=\"answer\">\n\n"
    "{{Definition/Translation}}\n\n{{Image}}\n\n{{Audio}}"
)

def _ankiconnect_request(action: str, params: dict = None, version: int = 6):
    payload = json.dumps({"action": action, "version": version, "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(ANKICONNECT_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("error") is not None:
        raise Exception(body["error"])
    return body["result"]

def _deck_name_for_language(language: str) -> str:
    return f"Decky Anki Plugin Deck ({language})"

# Builds an AnkiConnect picture/audio media entry from a user-typed path or URL, or None if
# empty. AnkiConnect distinguishes remote vs local media by which param is set: `url` for
# http(s), `path` for a local filesystem path (e.g. a file on the Deck itself).
def _build_media_entry(value: str, field_name: str):
    value = (value or "").strip()
    if not value:
        return None
    entry = {"fields": [field_name]}
    if value.lower().startswith(("http://", "https://")):
        filename = os.path.basename(urllib.parse.urlsplit(value).path) or f"decky_{field_name.lower()}"
        entry["url"] = value
    else:
        path = os.path.expanduser(value)
        filename = os.path.basename(path) or f"decky_{field_name.lower()}"
        entry["path"] = path
    entry["filename"] = filename
    return entry

class Plugin:
    # Lightweight ping to check whether Anki + AnkiConnect are reachable right now. Swallows
    # every error (connection refused, timeout, ...) and just reports false, since callers only
    # care about the yes/no status, not the specific failure.
    async def check_anki_connection(self) -> bool:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _ankiconnect_request, "version")
            return True
        except Exception as e:
            decky.logger.info(f"check_anki_connection: not connected: {e}")
            return False

    async def _ensure_deck_and_model(self, loop, deck_name: str) -> None:
        await loop.run_in_executor(None, _ankiconnect_request, "createDeck", {"deck": deck_name})
        existing_models = await loop.run_in_executor(None, _ankiconnect_request, "modelNames")
        if NOTE_TYPE_NAME not in existing_models:
            await loop.run_in_executor(None, _ankiconnect_request, "createModel", {
                "modelName": NOTE_TYPE_NAME,
                "inOrderFields": NOTE_TYPE_FIELDS,
                "css": NOTE_TYPE_CSS,
                "isCloze": False,
                "cardTemplates": [
                    {"Name": "Card 1", "Front": NOTE_TYPE_FRONT_TEMPLATE, "Back": NOTE_TYPE_BACK_TEMPLATE},
                ],
            })

    # Creates (or reuses, if already present) the plugin's per-language deck and shared note
    # type. Requires Anki running with AnkiConnect installed.
    async def create_deck_for_language(self, language: str) -> str:
        loop = asyncio.get_event_loop()
        try:
            deck_name = _deck_name_for_language(language)
            await self._ensure_deck_and_model(loop, deck_name)
            decky.logger.info(f"create_deck_for_language: ensured deck {deck_name!r} and note type {NOTE_TYPE_NAME!r}")
            return deck_name
        except (urllib.error.URLError, ConnectionError) as e:
            decky.logger.error(f"Could not reach AnkiConnect at {ANKICONNECT_URL}: {e}")
            raise Exception(f"Could not reach AnkiConnect. Is Anki running with AnkiConnect installed? ({e})")

    # Adds a note to the language's deck, creating the deck/note type first if needed. Image and
    # audio are AnkiConnect media refs (path or URL), not raw field text.
    async def add_note(self, language: str, morph: str, definition: str, image: str, audio: str) -> int:
        loop = asyncio.get_event_loop()
        try:
            if not morph or not morph.strip():
                raise Exception("Morph is required.")
            deck_name = _deck_name_for_language(language)
            await self._ensure_deck_and_model(loop, deck_name)

            note = {
                "deckName": deck_name,
                "modelName": NOTE_TYPE_NAME,
                "fields": {
                    "Morph": morph,
                    "Definition/Translation": definition,
                    "Image": "",
                    "Audio": "",
                },
                "options": {"allowDuplicate": False},
                "tags": [],
            }
            picture_entry = _build_media_entry(image, "Image")
            if picture_entry:
                note["picture"] = [picture_entry]
            audio_entry = _build_media_entry(audio, "Audio")
            if audio_entry:
                note["audio"] = [audio_entry]

            note_id = await loop.run_in_executor(None, _ankiconnect_request, "addNote", {"note": note})
            decky.logger.info(f"add_note: added note {note_id} to deck {deck_name!r}")
            return note_id
        except (urllib.error.URLError, ConnectionError) as e:
            decky.logger.error(f"Could not reach AnkiConnect at {ANKICONNECT_URL}: {e}")
            raise Exception(f"Could not reach AnkiConnect. Is Anki running with AnkiConnect installed? ({e})")

    # Asyncio-compatible long-running code, executed in a task when the plugin is loaded
    async def _main(self):
        self.loop = asyncio.get_event_loop()
        decky.logger.info("Hello World!")

    # Function called first during the unload process, utilize this to handle your plugin being stopped, but not
    # completely removed
    async def _unload(self):
        decky.logger.info("Goodnight World!")
        pass

    # Function called after `_unload` during uninstall, utilize this to clean up processes and other remnants of your
    # plugin that may remain on the system
    async def _uninstall(self):
        decky.logger.info("Goodbye World!")
        pass

    # Migrations that should be performed before entering `_main()`.
    async def _migration(self):
        decky.logger.info("Migrating")
        # Here's a migration example for logs:
        # - `~/.config/decky-template/template.log` will be migrated to `decky.decky_LOG_DIR/template.log`
        decky.migrate_logs(os.path.join(decky.DECKY_USER_HOME,
                                               ".config", "decky-template", "template.log"))
        # Here's a migration example for settings:
        # - `~/homebrew/settings/template.json` is migrated to `decky.decky_SETTINGS_DIR/template.json`
        # - `~/.config/decky-template/` all files and directories under this root are migrated to `decky.decky_SETTINGS_DIR/`
        decky.migrate_settings(
            os.path.join(decky.DECKY_HOME, "settings", "template.json"),
            os.path.join(decky.DECKY_USER_HOME, ".config", "decky-template"))
        # Here's a migration example for runtime data:
        # - `~/homebrew/template/` all files and directories under this root are migrated to `decky.decky_RUNTIME_DIR/`
        # - `~/.local/share/decky-template/` all files and directories under this root are migrated to `decky.decky_RUNTIME_DIR/`
        decky.migrate_runtime(
            os.path.join(decky.DECKY_HOME, "template"),
            os.path.join(decky.DECKY_USER_HOME, ".local", "share", "decky-template"))
