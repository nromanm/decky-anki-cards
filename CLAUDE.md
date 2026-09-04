# Decky Anki Cards

Decky Loader plugin for Steam Deck that adds cards to Anki decks.

## Architecture
- Frontend: React + TypeScript (`src/index.tsx`), built with rollup via `pnpm run build`.
  UI components come from `@decky/ui`. Output is `dist/index.js`.
- Backend: Python (`main.py`), runs on the Deck under Decky Loader.
  Calls AnkiConnect at http://127.0.0.1:8765. No compiled binary, no Docker.
- Frontend calls backend via `callable` from `@decky/api`.

## Workflow
- Build: `pnpm run build`
- Deploy to the Deck: `./scripts/deploy.sh` (rsync over SSH, restarts plugin_loader)
- Never commit `.env` — it holds the Deck's sudo password.

## Conventions
- Any frontend change requires a rebuild before it appears on the Deck.
