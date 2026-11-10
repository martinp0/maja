# Mája — provozní poznámky

Podrobný provozní popis (česky). Veřejný přehled, self-hosting a licence jsou v [README](../README.md).

# tractive-ingestor

Backend „Fáze 0 / MVP“ z konceptu vlastní psí appky (`tractive-app-koncept.md`).
Jeden dlouho běžící Python proces, který je **jediným klientem Tractive API**:

1. přihlásí se e-mailem a heslem a drží token (refresh token neexistuje → re-login),
2. stáhne seznam psů a trackerů a aktuální stav (poloha, baterie),
3. doplní **historii pozic** od posledního uloženého bodu (backfill),
4. drží **jedno** spojení na realtime kanál `channel.tractive.com` (reconnect, backoff, watchdog),
5. každou událost uloží **raw do `events` (jsonb)** a normalizovaně do `positions` / `hw_reports` / `health_daily`,
6. periodicky stahuje health overview (aktivita, spánek).

Telefon s Tractive nikdy nemluví – čte jen z naší DB přes **API + PWA** (`src/tractive_api`, `web/`,
viz [Appka](#appka-pwa--api)); nativní SwiftUI appka přijde později nad stejným API.

> ⚠️ Neoficiální, reverzně zjištěné API. Pravděpodobně v rozporu s ToS Tractive → jen pro osobní použití.
> API se může kdykoli změnit, proto ukládáme raw JSON a parsování je tolerantní.

## Prerekvizity

- **Python 3.11+** (ověřeno na 3.13)
- **Postgres 14+ s PostGIS** – ideálně Supabase (PostGIS zapnout v *Database → Extensions*), nebo lokální Postgres. DB je volitelná: bez `DATABASE_URL` běží ingestor v „dry-run“ režimu a jen loguje.
- **Tractive účet s e-mailem + heslem** a **aktivním Premium předplatným**.
  Přihlášení přes Apple/Google nefunguje – nastav si v Tractive heslo, nebo založ e-mailový účet a psa na něj nasdílej
  (pozor: sdílené trackery bez `details` aiotractive zatím přeskakuje, viz TODO).

## Instalace

```bash
cd tractive-ingestor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .            # balíček je v src/ layoutu → kvůli `python -m tractive_ingestor`
cp .env.example .env        # a vyplnit
```

### `.env`

| Proměnná | Význam |
|---|---|
| `TRACTIVE_EMAIL`, `TRACTIVE_PASSWORD` | přihlašovací údaje (jen e-mail/heslo účet) |
| `DATABASE_URL` | Postgres connection string. Supabase: *Project Settings → Database*. Pro dlouho běžící proces použij **direct** nebo **Session pooler (5432)**; transaction pooler (6543) funguje taky (vypínáme prepared statements), ale není ideální. Prázdné = bez DB. |
| `LOG_LEVEL`, `LOG_FORMAT` | `INFO`/`DEBUG`; `console` (vývoj) nebo `json` (systemd/Docker) |
| `BACKFILL_DAYS` | kolik dní historie stáhnout, pokud v DB nic není (default 7) |
| `BACKFILL_CHUNK_HOURS` | velikost jednoho okna požadavku na historii (default 24 h) |
| `HEALTH_POLL_INTERVAL_S` | interval stahování health overview – data o aktivitě/spánku, nesouvisí s `/healthz` (default 3600 s) |
| `HEALTH_HOST`, `HEALTH_PORT` | health server `/healthz` + `/readyz` (default `127.0.0.1:8080`; v Dockeru `0.0.0.0`; `HEALTH_PORT=0` = vypnuto) |
| `HEALTH_READY_MAX_AGE_S` | `/readyz` je 503, když z kanálu nepřišel event/keep-alive déle než N s (default 90) |
| `RECONNECT_BACKOFF_BASE_S`, `RECONNECT_BACKOFF_MAX_S` | exponenciální backoff reconnectu kanálu (full jitter) |
| `CHANNEL_MAX_SILENCE_S` | tvrdý reconnect, když nepřijde žádný event N s (0 = vypnuto; 60s keep-alive watchdog řeší aiotractive) |
| `FIXTURES_DIR` | Fáze 0: sem se zapisují raw payloady jako `*.jsonl` (prázdné = vypnuto) |
| `LOCAL_TZ` | časová zóna pro „den“ v `health_daily` (default `Europe/Prague`) |

## Spuštění

```bash
# 1) Fáze 0 – ověření účtu a zachycení payloadů (bez DB):
python -m tractive_ingestor check
#    → vypíše user_id, psy a trackery, stáhne pos/hw report a health, uloží fixtures/*.jsonl

# 2) Založení schématu v DB (idempotentní, lze pouštět opakovaně):
python -m tractive_ingestor init-db        # nebo: psql "$DATABASE_URL" -f src/tractive_ingestor/schema.sql

# 3) Služba:
python -m tractive_ingestor                # = run; Ctrl+C / SIGTERM = graceful shutdown
```

Exit kódy: `0` OK · `1` neočekávaná chyba · `2` chybí konfigurace · `3` Tractive odmítl přihlášení.

## Nasazení (RPi5 / Docker / systemd)

Návod v češtině: **[`deploy/README-rpi5.md`](../deploy/README-rpi5.md)** – Docker i holý systemd, Tailscale, Supabase.

| Soubor | K čemu |
|---|---|
| `Dockerfile` | multi-stage image na `python:3.13-slim` (amd64 i arm64), non-root, `ENTRYPOINT python -m tractive_ingestor`, default `run` |
| `docker-compose.yml` | `db` (PostGIS 16) + `ingestor` + `api` (FastAPI + PWA), `restart: unless-stopped` |
| `Dockerfile.api` | multi-stage: Node build PWA (`web/dist`) → venv s FastAPI → slim runtime, `ENTRYPOINT python -m tractive_api` |
| `.dockerignore` | do image nejde `.env`, `.venv`, `.git`, `fixtures` … |
| `deploy/tractive-ingestor.service` | systemd unit pro běh bez Dockeru (`/opt/tractive-ingestor`, user `tractive`, hardening, journald) |

Rychle s Dockerem:

```bash
cp .env.example .env && chmod 600 .env     # vyplnit TRACTIVE_* a POSTGRES_PASSWORD
docker compose up -d
docker compose run --rm ingestor init-db   # jednou při prvním spuštění
docker compose logs -f ingestor
```

## Healthcheck

Ve stejném asyncio procesu běží malý HTTP server (jen stdlib, `health.py`), startuje před loginem a končí se službou:

- **`GET /healthz` – liveness:** 200, dokud proces a event loop odpovídají. Na to míří Docker `HEALTHCHECK`
  (krátký výpadek Tractive nesmí vést k restartu kontejneru – reconnect řeší ingestor sám).
- **`GET /readyz` – readiness:** 200 jen když je aktivní Tractive session **a** kanál je připojený (handshake)
  **a** event, keep-alive nebo handshake přišel za posledních `HEALTH_READY_MAX_AGE_S` (90 s). Jinak 503 s JSON
  (`reasons`, `authenticated`, `channel_connected`, `last_event_age_s`, `last_keepalive_age_s`, `reconnects`,
  `last_error`, `phase`…). Pro externí monitoring, např. curl/Uptime Kuma přes Tailscale.

Keep-alive zprávy aiotractive zahazuje, ale drží si jejich čas – `TractiveClient.events()` proto vytváří
`aiotractive.Channel` sám a čas posledního keep-alive vystaví pro `/readyz`. Stav plní `HealthState`
(`ChannelConsumer` = spojení/eventy/reconnecty/chyby, `Ingestor` = fáze a přihlášení).

```bash
curl -s http://127.0.0.1:8080/readyz | python -m json.tool
python -m unittest discover -s tests -v     # testy health serveru, bez účtu i DB
```

## Povely (LIVE / bzučák / světlo)

Povely jdou přes frontu v tabulce `commands`; odesílá je jen běžící služba (jediný Tractive klient):

```bash
python -m tractive_ingestor enqueue <tracker_id> live on      # LIVE tracking (vyprší samo po ~5 min)
python -m tractive_ingestor enqueue <tracker_id> buzzer       # zvuk (akce default on)
python -m tractive_ingestor enqueue <tracker_id> led off      # světlo
# v Dockeru: docker compose exec ingestor python -m tractive_ingestor enqueue <tracker_id> live on
```

Stavy: `queued → sending → sent → done`; doma (power-saving / `KNOWN_WIFI`) cloudové povely neprojdou, takže
se povel odloží (`deferred`, `last_error=tracker_in_home_zone`) a zkouší se každých `COMMAND_DEFER_RETRY_S`,
dokud pes neodejde – po `COMMAND_DEFER_TIMEOUT_S` od zařazení `failed`. Chyba API = `deferred` s backoffem,
po `COMMAND_MAX_ATTEMPTS` voláních `failed`. `sent` → `done`, jakmile stream pošle odpovídající stav switche,
jinak po `COMMAND_CONFIRM_TIMEOUT_S` `done` s `last_error=unconfirmed`. Starý povel se neposílá (`expired`).

```sql
SELECT created_at, command, action, status, attempts, last_error FROM commands ORDER BY created_at DESC LIMIT 10;
```

## Appka (PWA + API)

Jeden kontejner `api` (port **8090**): FastAPI servíruje JSON na `/api/*` a postavenou PWA (`web/dist`) na `/`.
PWA tak volá same-origin `/api` a nepotřebuje CORS ani reverse proxy. API **jen čte** z DB (pool má
`default_transaction_read_only=on`); jediný zápis je zařazení povelu – vloží řádek `queued` do `commands` a odešle ho
běžící ingestor (Tractive API volá dál jen on). Do kontejneru `api` se nedává heslo k Tractive (žádný `env_file`).

> ⚠️ **Autentizace zatím není.** Ochranu dělá jen publikace portu na Tailscale IP Pi (`API_PUBLISH_ADDR`), nikdy `0.0.0.0`.

### Endpointy

| Metoda + cesta | Co vrací |
|---|---|
| `GET /api/health` | liveness (nesahá do DB) |
| `GET /api/ready` | readiness – 503, když DB neodpovídá |
| `GET /api/pets` | psi + `tracker_id` (bez `home_location`) |
| `GET /api/trackers` | trackery + poslední stav (baterie, `state`/`state_reason`, `sensor_used`, `last_position_ts`, jméno psa) |
| `GET /api/trackers/{id}/latest` | poslední poloha (`lat`, `lng`, `accuracy_m`, `ts`, `sensor_used`) + poslední hw_report (baterie, stav) + `in_home_zone` |
| `GET /api/trackers/{id}/positions?since=<ISO>&limit=N` | trasa chronologicky; default posledních 24 h, `limit` default 2000, strop `API_POSITIONS_MAX_LIMIT` (5000); `since` bez zóny = UTC. Read-only token: `since` se ořízne na posledních 48 h a `limit` na 2000 |
| `GET /api/trackers/{id}/health?days=7` | `health_daily` (aktivní minuty vs. cíl, spánek den/noc/klid, stavy) – `latest` + posledních N dní |
| `GET /api/trackers/{id}/commands?limit=N` | poslední povely se stavem (`queued/sending/sent/done/deferred/failed`, `last_error`) |
| `POST /api/trackers/{id}/commands` | body `{"command": "live\|buzzer\|led", "action": "on\|off"}` (validace `commands.parse_command`) → 201 + řádek povelu (`id`, `status: queued`, `in_home_zone`, `note`) |
| `GET /api/docs` | Swagger UI |

Neznámý tracker = 404, nevalidní povel = 422, nedostupná DB = 503.

Read-only token (`API_READ_TOKENS`, `jméno=token` nebo čárkami oddělený seznam) má vlastní rate limit
podle IP klienta. `CF-Connecting-IP` a `X-Forwarded-For` se berou jen když TCP peer je lokální proxy
(loopback nebo docker bridge `172.16.0.0/12`). Platný token se kvůli cizím neúspěchům nezamítne a do
logu jde jen jeho jméno. Z `/api/geofence/state` a podobných odpovědí token nedostane souřadnice domova
(cookie po PINu ano). Žádná změna schématu DB.

Odpovědi API i PWA nesou `Content-Security-Policy` (mapové dlaždice `tile.openstreetmap.org`, inline
styly kvůli Leafletu, `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff` a
`Referrer-Policy: no-referrer`. `Strict-Transport-Security` nastavuje edge (Cloudflare); aplikace ho
nepřidává ani nemaže.

### PWA (`web/`)

React + Vite + TypeScript, mapa Leaflet + OpenStreetMap dlaždice (bez API klíče), `vite-plugin-pwa` (manifest +
service worker, instalovatelná na plochu). Česky. Obsahuje: mapu s poslední polohou Máji (+ kruh přesnosti) a trasou
za 24 h, kartu stavu (baterie, stav trackeru, poslední aktualizace v Europe/Prague, zdroj polohy GPS vs. domácí WiFi),
aktivitu (prstenec minut vs. cíl, spánek) a tlačítka povelů (LIVE zap/vyp, zvuk, světlo zap/vyp) se seznamem posledních
povelů a jejich stavů; dokud je Mája doma, upozorní, že se povely odloží.

Polling: poloha 30 s, trasa 60 s, aktivita 5 min, povely 20 s (3 s, když nějaký čeká). Na pozadí se nepolluje.

```bash
cd web
npm ci
npm run dev        # http://localhost:5173, /api se proxuje na VITE_DEV_API (default http://127.0.0.1:8090)
npm run build      # typecheck + build do web/dist (ten pak servíruje API na /)
```

`VITE_API_BASE` (build-time) – prázdné = same-origin `/api` (doporučeno); jen pokud PWA hostuješ jinde, např.
`VITE_API_BASE=http://100.x.y.z:8090/api` (pak přidej origin do `API_CORS_ORIGINS`).

### Upozornění (Web Push)

Ingestor (`push.py`, `NotifyWorker` každých `PUSH_EVAL_INTERVAL_S` s) posílá push na všechna
zařízení v `push_subscriptions`:

| Kdy | Text |
|---|---|
| nová událost `left_home` | „Mája odešla z domova“ |
| nová událost `returned_home` | „Mája je zpátky doma 🏠“ + jak dlouho byla venku |
| away a dál než `GEOFENCE_ESCAPE_M` (1× za epizodu) | „🚨 Mája utekla! {m} m od domova“ |
| baterie < 20 % (znovu až po > 30 %) | „🔋 Slabá baterie obojku“ |

Idempotentní: `geofence_events.notified_at` a `notify_state` – restart nic nepošle znovu; události starší
než `PUSH_MAX_EVENT_AGE_S` (výpadek) se jen označí. Odběry s 404/410 se mažou.

Nastavení: `docker compose run --rm --no-deps ingestor gen-vapid >> .env` (VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY /
VAPID_SUBJECT), pak `docker compose run --rm ingestor init-db` (nové tabulky) a `docker compose up -d`.
V appce karta „Upozornění“ → Zapnout → Poslat test. **iPhone (iOS 16.4+): jen z appky přidané na plochu.**
API: `GET /api/push/vapid-public-key`, `POST /api/push/subscribe|unsubscribe|test` (za loginem).

### Přihlášení: heslo + Face ID (passkeys)

Heslo (`APP_PIN`) zůstává jako záloha. Po přihlášení heslem appka nabídne **Zapnout Face ID** (WebAuthn passkey,
platform authenticator, userVerification=required); karta „Přihlášení a Face ID“ ukazuje a maže passkeys.
Passkey funguje jen na `WEBAUTHN_RP_ID` (v Dockeru bez nastavení `localhost`, tedy `http://localhost:8090`) – ne na jiné adrese, než na které passkey vznikl. Na Tailscale IP nebo v LAN je potřeba HTTPS a origin v `.env`.
Endpointy: `POST /api/webauthn/register/options|verify` (jen přihlášený), `POST /api/webauthn/login/options|verify`
(veřejné, stejný per-IP rate limiter jako `/api/login`; read-only token má vlastní limiter), `GET /api/webauthn/credentials`,
`POST /api/webauthn/credentials/{id}/delete`; `/api/me` vrací `passkey_count`. Tabulka `webauthn_credentials`
(jen veřejné klíče) → po nasazení `init-db`.

Čerstvost dat: karta stavu ukazuje „Poslední poloha“ (čas fixu) a „Spojení s obojkem“ (`trackers.updated_at`).
Doma na Wi‑Fi v úsporném režimu obojek posílá polohu zřídka – appka to napíše a nabídne **Živá poloha** (povel LIVE).
`/api/*` má vždy `Cache-Control: no-store` a service worker je bere jen ze sítě (NetworkOnly).

### Spuštění API lokálně (bez Dockeru)

```bash
pip install -e '.[api]'            # nebo '.[dev]' kvůli testům (httpx)
(cd web && npm ci && npm run build)
DATABASE_URL=postgresql://… python -m tractive_api    # http://127.0.0.1:8090/  (API_HOST/API_PORT/API_WEB_DIST)
```

### Docker / RPi5

```bash
# v .env: API_PUBLISH_ADDR=<tailscale ip -4>   (100.x.y.z, jen na tom stroji)
docker compose up -d --build api
curl -fsS http://127.0.0.1:8090/api/ready    # z Pi (když je API_PUBLISH_ADDR loopback)
# z telefonu v tailnetu: http://<tailscale-ip>:8090/  → Safari → Sdílet → Přidat na plochu
```

PWA se staví uvnitř image (stage `web`, Node 22). Vite build má špičku ~0,5 GB RAM – na 2GB Pi vedle Postgresu
a ingestoru to může být těsné. Záloha: postav `web/dist` jinde (Mac), zkopíruj na Pi (`scp -r web/dist <pi-host>:~/tractive-ingestor/web/`)
a v `docker-compose.yml` odkomentuj `additional_contexts: webdist: ./web/dist` – stage s Node se pak vůbec nespustí.

| Proměnná | Význam |
|---|---|
| `API_PUBLISH_ADDR`, `API_PUBLISH_PORT` | kde compose publikuje port appky na hostu (default `127.0.0.1:8090`; pro telefon Tailscale IP Pi) |
| `API_HOST`, `API_PORT` | kde uvicorn poslouchá (default `127.0.0.1:8090`; v Dockeru `0.0.0.0:8090`) |
| `API_WEB_DIST` | adresář s buildem PWA (default `web/dist`; v image `/app/web/dist`); chybí = jen `/api` |
| `API_CORS_ORIGINS` | čárkami oddělené originy pro CORS. Prázdné nebo nenastavené = žádný CORS (same-origin PWA). Dev server `:5173` sem dej jen když ho prohlížeč volá napřímo |
| `API_DB_POOL_MIN`, `API_DB_POOL_MAX` | pool spojení API (default 1/3) |
| `API_POSITIONS_MAX_LIMIT`, `API_POSITIONS_DEFAULT_HOURS` | strop bodů trasy (5000) a default okno (24 h) |
| `VITE_API_BASE` | build arg PWA – base URL API (prázdné = `/api`) |

## Struktura

```
src/tractive_ingestor/
  __main__.py      CLI: run | check | init-db | enqueue
  config.py        nastavení z env/.env (pydantic-settings)
  logging_setup.py structlog (console/json)
  client.py        tenký wrapper nad aiotractive: login, re-login při 401, data, příkazy, events()
  channel.py       konzument realtime streamu: reconnect + backoff + watchdog + routing do handleru
  parsing.py       normalizace raw payloadů → PositionRecord / HwRecord / HealthRecord / SwitchState
  storage.py       asyncpg pool (lazy), idempotentní upserty; NullStorage pro běh bez DB
  health.py        HTTP health server (/healthz, /readyz) + HealthState
  commands.py      fronta povelů LIVE/buzzer/LED: validace, rozhodnutí doma/venku, CommandWorker
  ingestor.py      orchestrace: login → discovery → snapshot → backfill → stream + health loop → shutdown
  schema.sql       DDL (PostGIS): trackers, pets, events, positions, hw_reports, health_daily,
                   zones, zone_events, commands, share_links (+ RLS zapnuté bez politik)
  rules/geofence.py  kostra geofence (kruhové zóny, hystereze) – Fáze 1
src/tractive_api/
  app.py           FastAPI: /api/* (read + POST povelu), CORS, 503 při výpadku DB, PWA z web/dist na /
  store.py         asyncpg read-only pool; jediný zápis = INSERT do commands (status queued)
  config.py        API_* nastavení (pydantic-settings), sdílí DATABASE_URL a LOG_*
  __main__.py      `python -m tractive_api` → uvicorn
web/               PWA (React + Vite + TS, Leaflet/OSM, vite-plugin-pwa)
fixtures/          zachycené payloady (*.jsonl je v .gitignore – obsahují polohu domova!)
```

### Co dělá aiotractive a co my

aiotractive (v1.3.0) už řeší: auth na `graph.tractive.com/4/auth/token`, re-login, když tokenu zbývá < 1 h,
retry na HTTP 429, realtime kanál `channel.tractive.com/3/channel` včetně zahazování handshake/keep-alive
a 60s keep-alive watchdogu. My přidáváme: vynucený re-login při 401, nekonečný reconnect s backoffem,
backfill mezery po výpadku, ukládání a normalizaci.

## Stav: co funguje a co je TODO

Ověřeno: všechny moduly se importují bez DB i bez údajů, `py_compile` prochází, `check`/`run`
se špatnými údaji skutečně zkusí login u Tractive a skončí čistě kódem 3. `schema.sql` a upserty
jsou otestované proti lokálnímu Postgresu 17 + PostGIS (idempotence ověřena). Health server má
unit testy v `tests/test_health.py` (200/503/404/405, stale detekce, wiring s `ChannelConsumer`), parsování
reálných tvarů payloadů v `tests/test_parsing.py`, fronta povelů v `tests/test_commands.py`
(SQL proti Postgresu jen s `TEST_DATABASE_URL`), API v `tests/test_api.py` (FakeStore, bez DB; potřebuje `pip install -e '.[dev]'`).

**Ověřeno na reálných payloadech (Fáze 0, `check` 2026-10-19, tracker TG6C, fw `011.092-TRV6-009-e7ce01`):**
- `device_pos_report`: `time`/`time_rcvd` v unix **sekundách**, `latlong` [lat, lon], `pos_uncertainty` (m),
  `altitude` (m), `speed` může být `null`, `sensor_used` je volný string (viděno `KNOWN_WIFI`), `power_saving_zone_id`.
- `device_hw_report`: `time` (s), `battery_level`, `clip_mounted_state`/`temperature_state`/`hw_status` (null).
  Firmware a model v něm **nejsou** – jsou v tracker details (`fw_version`, `model_number`) → tabulka `trackers`.
- Tracker details: `capabilities`, `state`, `state_reason` (`POWER_SAVING`), `charging_state`, `battery_state`.
- Pet: `details` (name, pet_type, breed_ids, gender, birthday, weight v g, neutered, activity_settings), `home_location`.
- Health overview (aps-api, camelCase): aktivita vč. `hourlyDistribution`, spánek (den/noc/klid), stavy
  štěkání / klidové tepové a dechové frekvence / škrábání. Testy: `tests/test_parsing.py` (vzorky s fiktivními souřadnicemi).

**TODO – stále chybí reálné payloady:**
- **Historie pozic** `tracker/{id}/positions` (`format=json_segments`) – `check` ji nezachytil; `parse_history()` je
  zatím tolerantní odhad.
- **Realtime stream** (channel.tractive.com) – žádné eventy nepřišly (pes byl doma ve WiFi/power-saving zóně, skoro
  bez pohybu). Neznáme hodnoty `message`, tvar bloků position/hardware/switch ani jednotku `speed`.
  → **Potřeba další capture během skutečné procházky**: pes venku, zapnutý LIVE režim, `run` s `FIXTURES_DIR` aspoň
  na dobu procházky. Pak dotáhnout `parse_history()`, `position_from_event()`, `hw_from_event()` a router v `ingestor`.
- Health overview: ke kterému dni přesně patří (teď datum `activityDataSyncedAt` v `LOCAL_TZ`; `dayOffset` u
  jednotlivých metrik naznačuje, že se mohou vztahovat k různým dnům), historie po dnech.
- Odpovědi na příkazy LIVE/LED/bzučák doma vs. venku; jak se ve streamu projeví potvrzení/timeout.
- Platnost tokenu (viděno ~60 dní), chování u sdílených psů (aiotractive je přeskakuje).

**TODO – Fáze 1:**
- Povely: auto-prodlužování LIVE (vyprší po ~300 s), ověřit tvar potvrzení ve streamu (switch `active`).
- Geofence: zóny z DB, zápis `zone_events`, push (APNs); detekce útěku.
- RLS politiky pro appku, `share_links` přes `security definer` funkce.
- API/PWA: autentizace (teď jen Tailscale), push notifikace, živé aktualizace (SSE místo pollingu), výběr období trasy.
- Heartbeat do DB (monitoring bez přístupu k HTTP portu), metriky 429, testy nad fixtures.
- Sociální vrstva („Dog Tinder“: owner, dog, swipe, match, chat, place, presence…) **sem nepatří** – přijde jako samostatné schéma/služba, nezávislá na Tractive.

## Bezpečnost

- Refresh token neexistuje, takže **heslo k Tractive musí být uložené trvale**. Teď je v `.env` (je v `.gitignore`).
  Na RPi5 dej `.env` práva `600` a vlastníka servisního uživatele; časem přesunout do Supabase Vault / secret manageru.
- Používej **unikátní heslo** jen pro Tractive.
- Heslo je v kódu `SecretStr` – neobjeví se v logu ani v `repr()`. aiotractive na DEBUG loguje celé payloady
  (včetně polohy), proto ji logger drží minimálně na INFO.
- `fixtures/*.jsonl` a tabulka `positions` obsahují přesnou polohu psa = adresu domova. Necommitovat, nesdílet.
- Ingestor se do DB připojuje jako vlastník tabulek (obchází RLS) – connection string nikdy nedávat do appky.
