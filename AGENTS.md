# AGENTS.md

Cleanarr is a FastAPI + React app that reclaims disk from Radarr/Sonarr using watch history (Tautulli, Tracearr, Jellystat) and request data (Seerr / Overseerr / Jellyseerr).

## Layout

- `backend/app/` — FastAPI app. SQLite + poster cache under `DATA_DIR` (local `./data`, Docker `/data`).
- `backend/app/services/` — HTTP clients for each external app.
- `backend/tests/` — pytest. `conftest.py` must set `DATA_DIR` **before** importing `app` (`config` and `db` pin paths at import).
- `frontend/src/` — Vite + React 19. Almost everything lives in `App.tsx`; `api.ts` is the client.
- Docker image: Node 24 builds the UI, Python 3.14 serves it from `backend/static`. Entrypoint drops to `PUID`/`PGID`.

## Commands

```bash
make test                         # backend suite in a throwaway container
cd backend && pip install -r requirements-dev.txt && pytest --cov=app
cd frontend && npm ci && npm run typecheck && npm run build
```

Local run: API `uvicorn app.main:app --reload --port 7585` from `backend/`; UI `npm run dev` from `frontend/` (Vite proxies `/api` to 7585). Docker: `make up` (creates `.env` from `.env.example` if missing).

CI (`.github/workflows/ci.yml`) runs backend pytest + frontend typecheck/build on every push and PR. Python 3.14, Node 24.

## Domain rules (do not regress)

**Secrets.** Never return API keys (or passwords) from `/api/settings`. Saved keys stay blank in the UI. Do not commit `.env`.

**Settings.** `CLEANARR_HIDE_SETTINGS=1` hides all service URLs/keys, public links, and login from Settings (including unused services). `APP_SETTING_KEYS` (schedule + auto-delete) stay writable. Env-set values are locked even when the flag is off.

**Matching.** `CatalogIndex` matches tmdb → tvdb → imdb, then unique normalized title+year. Unmatched is Seerr ↔ *arr only. Ignore titles named `Unknown`. Skip Seerr blocklisted (status 6) and deleted (status 7) when deciding stale. `mediaAddedAt` alone is not proof Seerr still has the title. Seerr is keyed on TMDB, so a row with no TMDB id can never be added there.

**Ignored unmatched.** `unmatched_ignored` is a user preference: it survives a sync and `Clear library`, and only `DELETE /api/unmatched/ignored/{id}` removes an entry. Its key (kind, media type, tmdb, tvdb, lowercased title) deliberately leaves out the year so a metadata fix does not resurrect a row.

**Unmatched rows.** Each row carries a `kind` and a `seerr_state`. `seerr_missing` (states `available` / `orphan` / `requested`) is what Clear in Seerr acts on; `no_seerr` (`absent`) is what Add to Seerr acts on. `seerr_deleted` is history only: Seerr already let the title go so anyone can request it again — never clear it, and keep it out of the nav badge and the Library Unmatched card (`stats.actionable`, and `kind != 'seerr_deleted'`). A deleted status wins over a `claimed` flag from another payload in the same sync. Name Seerr-only rows before falling back to `TMDB <id>`: look the title up through Seerr's TMDB proxy first (capped by `SEERR_TITLE_LOOKUPS`).

**Watch history.** Tautulli / Tracearr / Jellystat can all be enabled; the same play (same person, same title, within two hours) counts once. Jellystat auth is **only** `x-api-token` — never `Authorization`, never the token in two header casings (403/401). Prefer a live Tautulli rating key over the first hit.

**Deletes.** Whitelist (title substring or TMDB id) always wins. Manual cleanup deletes *arr files and the Seerr media row; Ban also blocklists in Seerr. Automatic delete is **off by default**, runs only after a **scheduled** sync (never “Sync now”), skips whitelist, does not ban, caps per run, and uses Radarr/Sonarr `added_at`. Fail closed: no history source, a failed source, zero plays, missing `added_at`, or a play with no timestamp → do not auto-delete. Library **Stale / unwatched** must use the same candidate rule.

**UI.** Tabs are URL paths (`/users`, `/settings`, …). FastAPI must keep serving `index.html` for those so refresh works. Do not add a router library unless the UI is split up.

## Style

- Python 3.14, `from __future__ import annotations`, httpx for outbound calls.
- Keep service clients small; put cross-service logic in `sync.py`, `identity.py`, `match.py`, `actions.py`.
- Add or extend tests next to the behavior you change (`backend/tests/test_*.py`). Auto-delete, Seerr status, and client headers already have coverage — use those as the spec.
- Prefer a small, accurate change over a rewrite of `App.tsx` or `sync.py`.
