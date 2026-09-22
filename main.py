import os
import json
import re
import urllib.request
import urllib.error

# The decky plugin module is located at decky-loader/plugin
# For easy intellisense checkout the decky-loader code repo
# and add the `decky-loader/plugin/imports` path to `python.analysis.extraPaths` in `.vscode/settings.json`
import decky
import asyncio

ANKICONNECT_URL = "http://127.0.0.1:8765"

def _ankiconnect_request(action: str, params: dict = None, version: int = 6):
    payload = json.dumps({"action": action, "version": version, "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(ANKICONNECT_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("error") is not None:
        raise Exception(body["error"])
    return body["result"]

_TEMPLATE_PLACEHOLDER = re.compile(r"\{\{([^}]+)\}\}")

# Anki has no per-field "required" flag. The closest real signal: a note only fails to save if
# every card template's rendered Front side is empty, so a field referenced on a Front template
# is what actually needs a value to produce a card.
def _fields_used_on_front_templates(templates: dict) -> set:
    field_names = set()
    for template in templates.values():
        front_html = template.get("Front", "")
        for raw in _TEMPLATE_PLACEHOLDER.findall(front_html):
            name = raw.strip()
            if name.startswith(("#", "/", "^")):
                name = name[1:].strip()
            if ":" in name:
                name = name.rsplit(":", 1)[-1].strip()
            if name and name != "FrontSide":
                field_names.add(name)
    return field_names

class Plugin:
    # A normal method. It can be called from the TypeScript side using @decky/api.
    async def add(self, left: int, right: int) -> int:
        return left + right

    # Returns deck names from AnkiConnect. Requires Anki running with AnkiConnect installed.
    async def get_decks(self) -> list:
        loop = asyncio.get_event_loop()
        try:
            decks = await loop.run_in_executor(None, _ankiconnect_request, "deckNames")
            decky.logger.info(f"get_decks: found {len(decks)} decks: {decks}")
            return decks
        except (urllib.error.URLError, ConnectionError) as e:
            decky.logger.error(f"Could not reach AnkiConnect at {ANKICONNECT_URL}: {e}")
            raise Exception(f"Could not reach AnkiConnect. Is Anki running with AnkiConnect installed? ({e})")

    # Returns the note type (model) names actually used by notes in the given deck. Only reads
    # note IDs (findNotes) to check membership, never note/card field content.
    async def get_deck_models(self, deck_name: str) -> list:
        loop = asyncio.get_event_loop()
        try:
            all_models = await loop.run_in_executor(None, _ankiconnect_request, "modelNames")
            matching = []
            for model_name in all_models:
                query = f'deck:"{deck_name}" note:"{model_name}"'
                note_ids = await loop.run_in_executor(
                    None, _ankiconnect_request, "findNotes", {"query": query})
                if note_ids:
                    matching.append(model_name)
            decky.logger.info(f"get_deck_models: deck {deck_name!r} models {matching}")
            return matching
        except (urllib.error.URLError, ConnectionError) as e:
            decky.logger.error(f"Could not reach AnkiConnect at {ANKICONNECT_URL}: {e}")
            raise Exception(f"Could not reach AnkiConnect. Is Anki running with AnkiConnect installed? ({e})")

    # Returns the fields of the given note type (model), in modelFieldNames order, each with its
    # field description (shown in Anki's own "Add" dialog). Everything addNote later needs to
    # build a note: model name (caller already has it) + these field names. Schema only, no
    # note/card data read.
    async def get_model_fields(self, model_name: str) -> list:
        loop = asyncio.get_event_loop()
        try:
            names = await loop.run_in_executor(
                None, _ankiconnect_request, "modelFieldNames", {"modelName": model_name})
            try:
                descriptions = await loop.run_in_executor(
                    None, _ankiconnect_request, "modelFieldDescriptions", {"modelName": model_name})
            except Exception as e:
                decky.logger.warning(f"get_model_fields: modelFieldDescriptions unavailable for {model_name!r}: {e}")
                descriptions = [""] * len(names)
            try:
                templates = await loop.run_in_executor(
                    None, _ankiconnect_request, "modelTemplates", {"modelName": model_name})
                front_fields = _fields_used_on_front_templates(templates)
            except Exception as e:
                decky.logger.warning(f"get_model_fields: modelTemplates unavailable for {model_name!r}: {e}")
                front_fields = set()
            fields = [
                {"name": name, "description": description, "required": name in front_fields}
                for name, description in zip(names, descriptions)
            ]
            decky.logger.info(f"get_model_fields: model {model_name!r} fields {fields}")
            return fields
        except (urllib.error.URLError, ConnectionError) as e:
            decky.logger.error(f"Could not reach AnkiConnect at {ANKICONNECT_URL}: {e}")
            raise Exception(f"Could not reach AnkiConnect. Is Anki running with AnkiConnect installed? ({e})")

    async def long_running(self):
        await asyncio.sleep(15)
        # Passing through a bunch of random data, just as an example
        await decky.emit("timer_event", "Hello from the backend!", True, 2)

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

    async def start_timer(self):
        self.loop.create_task(self.long_running())

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
