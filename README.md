# Cleanarr

[![CI](https://github.com/michaelmurfy/cleanarr/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/michaelmurfy/cleanarr/actions/workflows/ci.yml)

Self-hosted UI to reclaim disk from Radarr and Sonarr. Watch history comes from Tautulli (Plex), Tracearr (Jellyfin/Plex/Emby), and/or Jellystat (Jellyfin). Seerr supplies who requested a title.

## Run

```bash
cp .env.example .env
# set CLEANARR_USERNAME, CLEANARR_PASSWORD, CLEANARR_SECRET
# fill the services you use; leave the rest blank
docker compose up -d --build
```

Open http://localhost:7585

The container drops to `PUID`/`PGID` (default `1000:1000`) and owns everything in
`/data`. Set them to your own user if you bind-mount `./data` instead of using the
named volume.

`CLEANARR_HIDE_SETTINGS=1` (default in `.env.example`) hides service URLs, API keys, and login from Settings — including unused services. Change them in `.env` and restart. Set it to `0` to manage connections in the UI.

## Delete

- Skips whitelist matches (title substring or TMDB id)
- Deletes the movie/series in Radarr or Sonarr with files on disk
- Removes the Seerr media record
- **Ban in Seerr + delete** also blacklists it in Seerr

## Local development

```bash
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload --port 7585

# another terminal
cd frontend && npm install && npm run dev
```

Vite proxies `/api` to port 7585. SQLite lives in `/data` in Docker (`cleanarr-data` volume).

## Tests

```bash
cd backend && pip install -r requirements-dev.txt
pytest --cov=app
```

CI runs the backend suite plus the frontend typecheck and build on every push and pull request.
