# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Decky Anki Cards

Decky Loader plugin for Steam Deck that will add cards to Anki decks. The repo is currently
still the stock [decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template)
scaffold (`main.py` and `src/index.tsx` contain the template's demo `add`/`start_timer` code,
not real Anki logic yet) with the dev/deploy workflow already wired up for this project.

## Commands
- Install deps: `pnpm i` (requires Node.js 16.14+ and pnpm v9 — install via `sudo npm i -g pnpm@9`)
- Build frontend: `pnpm run build` (rollup, config in `rollup.config.js` via `@decky/rollup`)
- Watch/rebuild on change: `pnpm run watch`
- Deploy to the Deck: `./scripts/deploy.sh` — builds, then rsyncs `dist`, `main.py`, `package.json`,
  `plugin.json`, `py_modules`, `README.md`, `LICENSE` to the Deck over SSH (host alias `steamdeck`)
  and restarts `plugin_loader`. Requires `DECK_PASS` in a local `.env` (never commit this file —
  it holds the Deck's sudo password).
- There is no real test suite; `pnpm run test` is an unconfigured stub that exits with an error.

## Architecture
- Frontend: React + TypeScript, entry point `src/index.tsx`, calls `definePlugin` from `@decky/ui`.
  Built by rollup to `dist/index.js`, which is the only frontend artifact Decky Loader loads.
  Any frontend change requires `pnpm run build` (or `watch`) before it appears on the Deck.
- Backend: Python, entry point `main.py`, defines a `Plugin` class whose methods Decky Loader
  runs directly on the Deck (no compiled binary, no Docker). Lifecycle hooks: `_main` (on load),
  `_unload`/`_uninstall` (teardown), `_migration` (runs before `_main`, for migrating
  legacy settings/logs/runtime data from older plugin versions).
- Frontend calls backend methods via `callable<Args, Return>("methodName")` from `@decky/api`;
  the backend can push events to the frontend via `decky.emit(...)` / `addEventListener`.
- Planned integration: the backend will talk to AnkiConnect at `http://127.0.0.1:8765` — not
  yet implemented.
- `py_modules/` holds vendored Python dependencies bundled with the plugin (none yet).
- `defaults/` holds static files (configs/templates) that ship alongside `dist/` and `main.py` in
  the distributed plugin zip, at the root of the extracted plugin directory.
- `backend/` (Makefile/Dockerfile/`src/main.c`) is the template's example of a *compiled* backend
  binary pipeline; this plugin doesn't use it (pure Python backend, no compiled binary).
