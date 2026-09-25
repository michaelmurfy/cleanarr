# Cleanarr

Self-hosted UI to reclaim disk from Radarr and Sonarr. Watch history comes from Tautulli (Plex), Tracearr (Jellyfin/Plex/Emby), and/or Jellystat (Jellyfin). Seerr supplies who requested a title.

## Security

**Do not expose Cleanarr to the public internet.** Keep port `7585` on your LAN or behind a VPN: no port-forwarding, and no public reverse proxy without strong auth in front of it. Tailscale or WireGuard works well, as does binding to localhost and reaching it over an SSH tunnel.

Built in:

- Sessions are signed, `HttpOnly`, and expire after 14 days. The signing key comes from your stored credentials, so changing the password or username signs every other session out.
- Sign-ins lock out for five minutes after eight failures, counted per client and username.
- Responses carry a content security policy and the usual hardening headers. API replies are `no-store`, and cross-site writes are rejected.
- API keys are stripped from log entries and connection-test messages, so an upstream error that quotes a URL cannot leak one.
- The session cookie sets `Secure` by itself over HTTPS. `CLEANARR_SECURE_COOKIE=1` forces it.

## Setup

### 1. Start the container

```bash
mkdir -p cleanarr && cd cleanarr
curl -fsSL -o docker-compose.yml https://raw.githubusercontent.com/michaelmurfy/cleanarr/main/docker-compose.yml
docker compose pull
docker compose up -d
```

Use `docker compose up -d`, **not** `up --build`. This file only pulls `ghcr.io/michaelmurfy/cleanarr:latest`; there is no Dockerfile in a compose-only install.

Or with Make from a full checkout: `make up`.

Images are published to [`ghcr.io/michaelmurfy/cleanarr`](https://ghcr.io/michaelmurfy/cleanarr) on every push to `main` (and on version tags). For a private package, `docker login ghcr.io` first. Override the image with `CLEANARR_IMAGE=…` if needed.

Open http://localhost:7585 and create a username and password. With no `.env`, Cleanarr also generates a random session secret and keeps it in the data volume.

### Unraid

Community Apps files live in this repository: `ca_profile.xml` and `templates/cleanarr.xml`. After they are on `main`, submit the repository at [ca.unraid.net/submit/new](https://ca.unraid.net/submit/new). The template pulls `ghcr.io/michaelmurfy/cleanarr:latest`, maps port `7585`, and stores the database in `/mnt/user/appdata/cleanarr`. `PUID`/`PGID` default to Unraid's `99`/`100`. Leave username, password, and the session secret blank to create the account in the web UI. Keep the web UI on your LAN.

### 2. Connect your services

Open **Settings** and enter URLs and API keys for the apps you use: Radarr (optional second instance for 4K), Sonarr, Seerr, and at least one watch-history source (Tautulli, Tracearr, Jellystat). Leave unused services blank, **Test** each row, then **Sync now**.

### 3. Optional `.env` (lock config to the host)

To keep secrets out of the UI, or to set `PUID`/`PGID`:

```bash
cp .env.example .env
# edit CLEANARR_USERNAME / CLEANARR_PASSWORD / CLEANARR_SECRET
# fill the services you want managed from the file
docker compose up -d
```

Set a strong `CLEANARR_SECRET` in `.env` when you use one; without an `.env`, Cleanarr stores a random secret in its data volume automatically.

Any value set in the environment (including from `.env`) is locked in Settings. Set `CLEANARR_HIDE_SETTINGS=1` to hide service URLs, API keys, public links, and login from Settings entirely, including unused services, and manage them only via `.env`.

The container drops to `PUID`/`PGID` (default `1000:1000`) and owns everything in `/data`. Set them to your own user if you bind-mount `./data` instead of using the named volume.

### Build locally

From a full git checkout (not needed if you only have `docker-compose.yml`):

```bash
docker build -t ghcr.io/michaelmurfy/cleanarr:latest .
docker compose up -d
# or: make build
# or: docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

## Automatic delete

Off by default, under Settings > Automatic sync. When on, each **scheduled** sync
deletes titles Radarr/Sonarr added more than the cutoff ago (default 1 year) that
nobody has watched since, files included, capped at 10 per run. Whitelisted titles
are skipped, nothing is banned in Seerr, and a manual "Sync now" never deletes.

A title played at an unknown time never qualifies, so missing data fails closed.

Age comes from the Radarr/Sonarr `added` date, stored on the next sync. Titles
without one are never auto-deleted, so an upgraded install deletes nothing until it
has synced.

The Library tab's **Stale / unwatched** filter uses the same rule, so with automatic
delete off it shows exactly the set a run would remove. Set the same cutoff to
preview it.

A run is skipped entirely if the watch history cannot be trusted: no history source
configured, one that failed during the sync, or all of them reporting zero plays.
That stops a Jellystat/Tautulli outage from making the whole library look unwatched.

## Updates

Upgrade with `make update`, or `docker compose pull && docker compose up -d`.

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

## Make

```bash
make up      # pull GHCR image and start
make env    # create .env from .env.example if missing
make build  # build locally and start
make test   # backend suite in a throwaway container
make pull   # refresh the published Cleanarr image
make update # pull latest published image and restart
```

## Tests

```bash
cd backend && pip install -r requirements-dev.txt
pytest --cov=app
```

CI runs the backend suite plus the frontend typecheck and build on every push and pull request. Docker images are built on PRs and pushed to GHCR from `main` and version tags.
