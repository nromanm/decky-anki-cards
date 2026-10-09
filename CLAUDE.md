# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Decky Anki Cards

Decky Loader plugin for Steam Deck that creates Anki flashcards from gameplay: captures the
last Steam screenshot as the card's image, runs bundled offline OCR (Tesseract) on it for an
example sentence, pulls audio out of the last saved Steam background-recording clip, and pushes
it all to a local Anki install via AnkiConnect's HTTP API (`http://127.0.0.1:8765`).

## Commands
- Install deps: `pnpm i` (requires Node.js 16.14+ and pnpm v9 — install via `sudo npm i -g pnpm@9`)
- Build frontend: `pnpm run build` (rollup, config in `rollup.config.js` via `@decky/rollup`)
- Watch/rebuild on change: `pnpm run watch`
- Deploy to the Deck: `./scripts/deploy.sh` — builds, then rsyncs `dist`, `main.py`, `package.json`,
  `plugin.json`, `py_modules`, `bin`, `README.md`, `LICENSE` to the Deck over SSH (host alias
  `steamdeck`) and restarts `plugin_loader`. Requires `DECK_PASS` in a local `.env` (never commit
  this file — it holds the Deck's sudo password).
- Python logic tests: `python3 tests/test_concat_audio_segments.py` — stubs the `decky` module
  (only available inside the real Decky Loader runtime) so `main.py` can be imported standalone
  to test pure logic. Currently covers the audio-chunk-concatenation ordering, the one piece of
  parser-like logic that would silently produce scrambled audio if it regressed.
- `pnpm run test` is an unconfigured JS stub that exits with an error — no frontend test suite.

## Architecture
- Frontend: React + TypeScript, entry point `src/index.tsx`, calls `definePlugin` from `@decky/ui`.
  Built by rollup to `dist/index.js`, which is the only frontend artifact Decky Loader loads.
  Any frontend change requires `pnpm run build` (or `watch`) before it appears on the Deck.
- Backend: Python, entry point `main.py`, defines a `Plugin` class whose methods Decky Loader
  runs directly on the Deck (no compiled binary, no Docker). Lifecycle hooks: `_main` (on load),
  `_unload`/`_uninstall` (teardown), `_migration` (runs before `_main`, for migrating
  legacy settings/logs/runtime data from older plugin versions).
- Frontend calls backend methods via `callable<Args, Return>("methodName")` from `@decky/api`.
- The backend process is a stripped-down child of Decky's plugin loader with a minimal
  environment (no `DISPLAY`/`WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR`) and runs as the `deck` user, not
  root, regardless of the `_root` flag in `plugin.json`. Bundled tools are invoked by absolute
  path (`subprocess.run`), never assumed to be on `PATH`.
- AnkiConnect note type: `Decky Anki Plugin Note Type`, fields `Morph, Definition/Translation,
  Example, Translation, Image, Audio` (`NOTE_TYPE_FIELDS` in `main.py`). `_ensure_deck_and_model`
  creates the deck/note type if missing, and migrates an existing note type by adding whatever
  fields it's missing via AnkiConnect's `modelFieldAdd` — safe to add new fields to
  `NOTE_TYPE_FIELDS` later without breaking existing users' note types.
- `bin/` holds a bundled static (musl-linked) Tesseract OCR binary and `tessdata_fast` language
  files for every language in the frontend's language selector (see `bin/NOTICE.md` for
  provenance/licensing) — a static build was necessary because SteamOS's glibc is older than
  current official Arch Linux packages require. Used for the "Example" field (OCR text off the
  current screenshot); invoked the same absolute-path-`subprocess.run` way as system `ffmpeg`/
  `ffprobe` (used to extract audio from Steam's segmented DASH game-recording clips — see the
  `_find_latest_recording_video_dir`/`_concat_audio_segments` comments in `main.py` for the
  on-disk clip format).
- `py_modules/` holds vendored Python dependencies bundled with the plugin (none yet).
- `defaults/` holds static files (configs/templates) that ship alongside `dist/` and `main.py` in
  the distributed plugin zip, at the root of the extracted plugin directory (currently unused
  beyond its own placeholder notes).
- Optional background service: `scripts/setup-anki-service.sh`, run manually once by the user
  directly on the Deck (never by the plugin itself) — sets up a systemd `--user` service that
  starts Anki headless on Gaming Mode boot. The plugin only ever launches Anki itself via a
  Steam non-Steam-game shortcut + `RunGame` (the "Open Anki" button) — the only method confirmed
  to reliably bring up AnkiConnect; see the comment above `ensureAnkiShortcut` in `src/index.tsx`
  for what else was tried and ruled out.
