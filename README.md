# Cleanarr

Self-hosted web app to reclaim disk from Radarr and Sonarr. It works in a desktop browser and on a phone. Watch history comes from Tautulli (Plex), Tracearr (Jellyfin/Plex/Emby), and/or Jellystat (Jellyfin). Seerr supplies who requested a title.

## Security

**Do not expose Cleanarr to the public internet.** Keep port `7585` on your LAN or behind a VPN: no port-forwarding, and no public reverse proxy without strong auth in front of it. Tailscale, WireGuard, or [Cloudflare Zero Trust](https://www.cloudflare.com/zero-trust/) (an Access application in front of the web UI) works well, as does binding to localhost and reaching it over an SSH tunnel.

Cleanarr does not include analytics or telemetry. It does not phone home, and it does not send your library, watch history, or settings anywhere except the Radarr, Sonarr, Seerr, and watch-history services you configure. AI was used to help create this app. That assistance happened during development; the running container does not call an AI service.

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

Cleanarr is in Community Apps. Open the **Apps** tab, search for Cleanarr, and install the template that uses `ghcr.io/michaelmurfy/cleanarr`. Another app also named Cleanarr cleans duplicate files in Plex; that one is a different project.

The template maps port `7585` and stores the database in `/mnt/user/appdata/cleanarr`. `PUID` and `PGID` default to Unraid's `99` and `100`. Leave the username, password, and session secret blank, then create the account in the web UI on first launch. Keep the web UI on your LAN. Updates are the usual Community Apps check for updates.

### 2. Connect your services

Open **Settings** and enter URLs and API keys for the apps you use: Radarr (optional second instance for 4K), Sonarr, Seerr, and at least one watch-history source (Tautulli, Tracearr, Jellystat). Leave unused services blank, **Test** each row, then **Sync now**.

### Single sign-on (Pocket ID)

Cleanarr can sign you in through [Pocket ID](https://pocket-id.org) or any other OpenID Connect provider.

1. In Pocket ID, add an OIDC client. Set its callback URL to `https://<your-cleanarr>/api/auth/oidc/callback` (Settings > Single sign-on shows the exact value). Restrict it to a user group if only some people should get in.
2. In Cleanarr, open **Settings > Single sign-on** and enter the issuer URL (your Pocket ID address, e.g. `https://id.example.com`), the client ID and the client secret. Save.
3. The sign-in page now has a **Sign in with Pocket ID** button.

The issuer, client ID and client secret can also come from `OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` in `.env`.

- `OIDC_ALLOWED_GROUPS` / `OIDC_ALLOWED_USERS` (comma separated) limit who gets in by group, username or email. Left blank, anyone the provider lets through is allowed.
- `OIDC_BUTTON_LABEL` changes the button text.
- `OIDC_REDIRECT_URI` pins the callback URL. Without it, Cleanarr builds the URL from the request and honours `X-Forwarded-Proto` / `X-Forwarded-Host`.
- `CLEANARR_DISABLE_PASSWORD_LOGIN=1` turns off password sign-in while single sign-on is configured. It is env-only, so a bad Settings change cannot lock you out.

Sign-in uses the authorization code flow with PKCE. The ID token's signature, issuer, audience, expiry and nonce are all checked. Changing the provider, client or allow lists signs out every single sign-on session. Single sign-on settings are part of the login, so they are hidden by `CLEANARR_HIDE_SETTINGS=1` and left out of backups.

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

## License

The repository is [MIT](LICENSE): the application source, Unraid templates, metadata, and docs. Upstream components inside the container image keep their own licenses.
