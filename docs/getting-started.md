# Run Mája locally

Mája is a small website for one [Tractive](https://tractive.com) GPS collar.
Docker starts three pieces: Postgres (the database), an ingestor (the only
program that talks to Tractive), and the website on port 8090.

You can follow this on a Mac, a Windows PC, a Linux computer, or a 64-bit
Raspberry Pi. The phone never talks to Tractive. It only opens the site you run.

Česky: [getting-started.cs.md](getting-started.cs.md).

## What you need

- **Docker**, with the Compose plugin.
  - Mac or Windows: install [Docker Desktop](https://docs.docker.com/get-docker/) and start it. Leave it running.
  - Linux or a Raspberry Pi: install Docker Engine and the Compose plugin (`docker compose version` should print a version). The Pi must be **64-bit** (`uname -m` prints `aarch64`). A 32-bit Pi OS will not run these images.
- **A Tractive account** with an **email and password** (not Sign in with Apple or Google) and an **active subscription**, if you want live collar data. You can skip this and load a fake dog instead. See [Try it without a Tractive account](#try-it-without-a-tractive-account).
- A few gigabytes of free disk. The first build downloads Postgres, Python, and Node.

## 1. Clone

```bash
git clone https://github.com/martinp0/maja
cd maja
```

## 2. Create `.env`

```bash
cp .env.example .env
chmod 600 .env
```

`.env` stays on your machine. Do not commit it. Open it in any editor.

### Required for a real collar

| Variable | What to put |
|---|---|
| `TRACTIVE_EMAIL` | The email on the Tractive account. |
| `TRACTIVE_PASSWORD` | That account's password. Use a password that exists only for this. Apple or Google sign-in will not work. |
| `POSTGRES_PASSWORD` | A database password. `.env.example` ships with `change-me-too`. Change it. Do not use `@`, `:`, `/`, `?`, or `#` — the password is placed inside a URL. |

`POSTGRES_USER` and `POSTGRES_DB` can stay `tractive`.

### Required before anyone else can open the site

| Variable | What to put |
|---|---|
| `APP_PIN` | A long passphrase. Empty means the site has **no login**. Fine on your own laptop. Not fine if the port is reachable by other people. |
| `APP_SESSION_SECRET` | Cookie key. Generate one with `openssl rand -hex 32`. If you leave it empty, everyone is logged out each time the API container restarts. |

### Optional

| Variable | Empty means |
|---|---|
| `HOME_LAT`, `HOME_LON` | No home circle and no leave/return history. These are your home coordinates. Keep them in `.env` only. `seed-demo` can print a **park** point for a demo circle — that point is not a house. |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | Push notifications off. |
| `WEBAUTHN_RP_ID`, `WEBAUTHN_ORIGIN` | Passkeys use `localhost` and `http://localhost:8090`. |
| `API_PUBLISH_ADDR` | The site is only on this computer: `127.0.0.1:8090`. |
| `API_PUBLISH_PORT` | Port `8090`. |
| `DEMO_MODE` | The ingestor tries to log in to Tractive. Set `DEMO_MODE=1` to skip that. |
| `INGESTOR_DATABASE_URL` | Compose uses the Postgres container. Set this only if you want Supabase instead, and then start the ingestor alone. |

`VAPID_SUBJECT` falls back to `mailto:you@example.com` when it is unset. Put your own `mailto:` in `.env` before you rely on push.

### Passkeys: localhost or your own domain

A passkey is tied to the exact site where you created it.

- On this computer, leave the variables unset. Compose uses `WEBAUTHN_RP_ID=localhost` and `WEBAUTHN_ORIGIN=http://localhost:8090`.
- On your own domain, set both, with no extra path and no trailing slash:

  ```bash
  WEBAUTHN_RP_ID=tracker.example.com
  WEBAUTHN_ORIGIN=https://tracker.example.com
  ```

  `WEBAUTHN_RP_ID` is the hostname only (no `https://`, no port). `WEBAUTHN_ORIGIN` is the full address you type in the browser. More than one origin can be comma-separated; a passkey still belongs to a single RP ID.

### Push keys

After the first image exists (the next section), generate a keypair and paste the output into `.env`:

```bash
docker compose run --rm --no-deps ingestor gen-vapid --subject mailto:you@example.com
```

Push stays off until those lines are in `.env` and you recreate the containers (`docker compose up -d`).

## 3. Start

```bash
docker compose up -d --build
```

That one command builds the ingestor, builds the website (the `web/` app is compiled inside the API image), and starts Postgres. You do not run `npm` yourself, and you do not need a pre-built `web/dist`.

The first build takes several minutes. Later starts are quicker.

On a Raspberry Pi with about 2 GB of RAM the Node build can be killed. If that happens, build `web/dist` on another computer (`cd web && npm ci && npm run build`), copy the `web/dist` folder to the Pi, and uncomment this block under the `api` service in `docker-compose.yml`:

```yaml
additional_contexts:
  webdist: ./web/dist
```

Leave the block in the file. A normal computer should keep it commented so Compose builds the site itself.

## 4. Create the tables

```bash
docker compose run --rm ingestor init-db
```

Safe to run again. It only adds missing tables.

## 5. Open the site

On the same computer, open <http://localhost:8090>.

- If `APP_PIN` is empty, you land on the map.
- If you set `APP_PIN`, the passphrase screen comes first. Type that passphrase.

The address is `127.0.0.1` only, so another phone on the Wi-Fi cannot open it yet. That is deliberate. See [Your own domain or tailnet](#your-own-domain-or-tailnet).

### When the first positions show up

With a real collar, watch the ingestor:

```bash
docker compose logs -f ingestor
```

You want a login, then a backfill of history (`BACKFILL_DAYS`, default 7), then a streaming phase. Refresh the site. The first backfill can take a few minutes. A collar that is home on Wi-Fi in power-saving sends points rarely; the map can look quiet even when login succeeded.

### The sample password does not log in

`.env.example` contains `TRACTIVE_EMAIL=you@example.com` and `TRACTIVE_PASSWORD=change-me`. Tractive rejects those. The ingestor exits and Docker starts it again. **The website and `/api/health` still work.** The map stays empty until a real login succeeds, or until you load the demo dog.

`docker compose up -d --build` is still successful in that state. Check `docker compose ps`: `db` and `api` should become healthy. `ingestor` may show `Restarting`. That is the bad password, not a broken install.

## Try it without a Tractive account

This inserts one fictional dog. It does not call Tractive and it does not use a home address.

In `.env`:

```bash
DEMO_MODE=1
```

You can leave the sample email in place. Demo mode does not send it anywhere.

```bash
docker compose up -d --build
docker compose run --rm ingestor seed-demo
```

`seed-demo` creates the tables if needed and replaces only the demo dog (tracker `DEMO-LUNA`, name Luna). Other collars in the same database are left alone. Run it again any time to refresh her walk so the points stay inside the last 24 hours.

The command prints `HOME_LAT` and `HOME_LON`. Those numbers are Bethesda Fountain in Central Park, New York. Paste them into `.env` only if you want the dashed circle and the leave/return list. Then recreate the API so it reads them:

```bash
docker compose up -d api
```

Leave `HOME_LAT` and `HOME_LON` empty if you do not want a zone. When you switch to a real collar, delete `DEMO_MODE` (or set it to `0`) and put **your** home in `.env` if you want that feature. Do not commit those lines.

Remove the demo dog without touching anything else:

```bash
docker compose run --rm ingestor seed-demo --clear
```

## Logs and health

```bash
docker compose ps
docker compose logs -f --tail=100 ingestor
docker compose logs -f --tail=100 api
curl -fsS http://127.0.0.1:8090/api/health
curl -fsS http://127.0.0.1:8090/api/ready
curl -fsS http://127.0.0.1:8080/healthz
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/readyz
```

| Check | Healthy when |
|---|---|
| `docker compose ps` → `api` healthy | The site process answers `/api/health`. |
| `GET /api/health` is 200 | The API process is up. It does not look at the database or at Tractive. |
| `GET /api/ready` is 200 | The API can query Postgres. |
| `GET /healthz` on port 8080 is 200 | The ingestor process is up. A bad Tractive password still looks like this until the process exits. |
| `GET /readyz` on port 8080 is 200 | Logged in to Tractive and the live channel is fresh. **503 is normal** while the first backfill runs, in demo mode, or when the password is wrong. |

Port 8080 is the ingestor, on this computer only. Port 8090 is the website.

## Update

```bash
cd maja
git pull
docker compose up -d --build
docker compose run --rm ingestor init-db
```

`init-db` is safe when the schema did not change.

## Stop

```bash
docker compose stop          # pause; data stays
docker compose down          # remove containers; the database volume stays
docker compose down -v       # also deletes the database volume
```

Containers are set to restart unless you stop them, including after a reboot, when Docker itself starts on boot.

## Back up the database

The database lives in a Docker volume named `tractive_pgdata` (project name `tractive`, volume `pgdata`).

A logical dump is the one to keep:

```bash
docker compose exec -T db pg_dump -U tractive -d tractive --no-owner > maja-backup.sql
```

If you changed `POSTGRES_USER` or `POSTGRES_DB`, use those names.

Restore into the running database (stop the site first so nothing writes during the restore):

```bash
docker compose stop api ingestor
docker compose exec -T db psql -U tractive -d tractive < maja-backup.sql
docker compose start api ingestor
```

A file copy of the volume is the other option. Stop Postgres first so the files are consistent:

```bash
docker compose stop db
docker run --rm -v tractive_pgdata:/v -v "$PWD":/backup alpine \
  tar czf /backup/pgdata.tgz -C /v .
docker compose start db
```

Positions are your dog's location. Store backups somewhere private.

## Your own domain or tailnet

Keep `API_PUBLISH_ADDR` at `127.0.0.1` when a tunnel reaches the site from outside. Set `APP_PIN` and `APP_SESSION_SECRET` before that. Do not bind `0.0.0.0` unless the passphrase is set.

### Cloudflare Tunnel

Install `cloudflared` from Cloudflare's docs. A quick public URL (it changes every run):

```bash
cloudflared tunnel --url http://127.0.0.1:8090
```

For a hostname you own, create a named tunnel in the Zero Trust dashboard and point the public hostname at `http://127.0.0.1:8090`. Then set `WEBAUTHN_RP_ID` and `WEBAUTHN_ORIGIN` to that hostname, as in [Passkeys: localhost or your own domain](#passkeys-localhost-or-your-own-domain).

### Tailscale

Install Tailscale, run `tailscale up`, then on this machine:

```bash
tailscale ip -4
```

Put that `100.x.y.z` address in `.env` as `API_PUBLISH_ADDR` and run `docker compose up -d`. Another device on the same tailnet opens `http://100.x.y.z:8090`. No router port-forward.

Passkeys do not work on that plain `http://100.x…` URL. Use HTTPS (Tailscale's HTTPS name, or your own domain) and set `WEBAUTHN_*` to that exact origin. A passkey created on `localhost` will not unlock the tailnet address.

## Install the app on a phone

The site is a PWA.

- Android (Chrome): menu → **Install app** or **Add to Home screen**.
- iPhone (Safari): Share → **Add to Home Screen**.

Push on iOS (16.4 and newer) is delivered only to the icon you added, and only over HTTPS. A phone cannot use your laptop's `http://localhost`.

## HTTPS, passkeys, and push

| | `http://localhost` | `http://` plus an IP or a tailnet name | `https://` your domain |
|---|---|---|---|
| Map, login, commands | Yes | Yes, if the port is published there | Yes |
| Passkeys (Face ID / Touch ID) | Yes | No | Yes, when `WEBAUTHN_*` matches the site |
| Web Push | Browsers allow it on localhost | Usually no | Yes, and on iOS only from the home-screen icon |

Service workers are part of the installed app. They need a secure page: localhost or HTTPS.

## Troubleshooting

### Tractive login fails

- The account must have its **own email and password**. Sign in with Apple or Google cannot be used here. In the Tractive app, set a password, or share the dog to an email account you control.
- The subscription has to be active.
- `docker compose logs ingestor` shows the rejection. The process exits with code 3 and Docker restarts it. `db` and `api` can still be healthy.
- `DEMO_MODE=1` skips login on purpose. Turn it off when you want the real collar.
- `GET /readyz` stays 503 until login and the live channel are both fine.

### No positions

- Run `docker compose run --rm ingestor init-db` if you have not.
- With the sample password, nothing will ever arrive. Put in the real account, or use `seed-demo`.
- `DEMO_MODE=1` without `seed-demo` is an empty map on purpose.
- The backfill may still be running. Follow `docker compose logs -f ingestor`.
- The default map window is the last 24 hours. `seed-demo` rewrites Luna's walk relative to "now"; run it again if you left the demo sitting for a day.
- A collar in power-saving at home can go a long time without a new GPS point.

### Port already in use

Another program has 8090 or 8080. Either stop it, or set `API_PUBLISH_PORT` / `HEALTH_PUBLISH_PORT` in `.env` and run `docker compose up -d` again. The URL changes with the port (`http://localhost:8091` if you picked 8091).

### ARM and x86

Official Python and Node images used here exist for `linux/amd64` and `linux/arm64`. Docker Desktop on Apple Silicon and a 64-bit Pi are arm64. A typical PC is amd64. Compose builds for the machine you are on.

`uname -m` on the Pi must be `aarch64`. If a container exits immediately with `exec format error`, the image was built for the other architecture. Run `docker compose build --no-cache` on the machine that will run it.

The optional `additional_contexts: webdist` path is the same on every architecture. Do not rename that key or the `api` service. A Pi that already uncomments `webdist` and sets `WEBAUTHN_*` in `.env` keeps working.

### Passkeys do not work on http

Face ID needs a secure context.

- `http://localhost:8090` and `http://127.0.0.1:8090` work when `WEBAUTHN_RP_ID=localhost` and `WEBAUTHN_ORIGIN` is that exact address (include the port).
- `http://192.168.x.x` and `http://100.x.y.z` do not. Put the site behind HTTPS and set `WEBAUTHN_RP_ID` / `WEBAUTHN_ORIGIN` to the site you actually open.
- A passkey registered on localhost will not appear on your domain. Register it again there.

### The site does not load

- Docker Desktop is running.
- `docker compose ps` shows `api` up. If it is missing, `docker compose logs api`.
- You are on the same machine, using `http://localhost:8090`, unless you changed `API_PUBLISH_ADDR`.
- `curl -fsS http://127.0.0.1:8090/api/health` prints JSON. If curl works and the browser does not, try a private window (an old service worker can hold a broken page).

## Screenshots

The pictures are the demo dog Luna on a walk around Bethesda Fountain in Central Park, New York. A public park, not a home, and not data from a Tractive account.

![Phone map, light](screenshots/map-light.webp)

![Phone map, dark](screenshots/map-dark.webp)

![Desktop, sidebar beside the map](screenshots/desktop.webp)

![Leave and return history](screenshots/history.webp)

![Settings](screenshots/settings.webp)

![Passphrase login](screenshots/login.webp)
