# Cleanarr

Cleanarr is a small self-hosted UI for reclaiming disk from your *arr stack. It merges watch history from **Tautulli** (Plex) and/or **Tracearr** (Jellyfin/Plex/Emby), shows who requested a title in **Seerr**, and can bulk-delete from **Radarr** / **Sonarr** (including files on disk). A whitelist keeps cult titles like *Stargate* or *Back to the Future* off the chopping block. Ban sends the title to the Seerr blacklist.

## Run with Docker

```bash
cp .env.example .env
# set CLEANARR_USERNAME / CLEANARR_PASSWORD / CLEANARR_SECRET
docker compose up -d --build
```

Open [http://localhost:7585](http://localhost:7585), sign in, then add service URLs and API keys under Settings. Sync builds the library.

You can leave Tautulli or Tracearr empty. If both are configured, the same play on Plex and Jellyfin (same person, same title, within two hours) counts once.

## What delete does

- Skips anything matching the whitelist (title substring or TMDB id)
- Deletes the movie/series in Radarr or Sonarr with `deleteFiles=true`
- Removes the Seerr media record
- Optional **Ban in Seerr + delete** also blacklists it in Seerr and adds an import-list exclusion

## Local development

```bash
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload --port 7585

# another terminal
cd frontend && npm install && npm run dev
```

Vite proxies `/api` to port 7585. The Docker image serves the built UI from FastAPI.

## Data

SQLite lives in `/data` inside the container (`cleanarr-data` volume). That holds login, settings, whitelist, and the last synced library.
