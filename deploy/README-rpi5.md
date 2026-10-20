# Nasazení na Raspberry Pi 5

Cíl: ingestor běží 24/7 doma na RPi5 (arm64, Raspberry Pi OS / Debian bookworm), přístup zvenku přes Tailscale.
Dvě cesty – **Docker (doporučeno)** nebo **holý venv + systemd**. Vyber jednu.

> Nejdřív si ověř účet: `python -m tractive_ingestor check` (lokálně, nebo `docker compose run --rm ingestor check`).
> Musí to být Tractive účet s **e-mailem a heslem** (ne Apple/Google) a aktivním předplatným.

---

## A) Docker (doporučeno)

```bash
# 1) Docker + compose plugin (oficiální skript, podporuje arm64)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER      # pak se odhlas/přihlas

# 2) Repo
git clone https://github.com/martinp0/maja.git ~/tractive-ingestor
cd ~/tractive-ingestor

# 3) Konfigurace
cp .env.example .env
nano .env                          # TRACTIVE_EMAIL, TRACTIVE_PASSWORD, POSTGRES_PASSWORD
chmod 600 .env

# 4) Start (první build na RPi5 trvá pár minut)
docker compose up -d

# 5) Jednou: založit schéma v DB (idempotentní, lze pustit znovu)
docker compose run --rm ingestor init-db

# 6) Logy (JSON)
docker compose logs -f ingestor
```

Aktualizace:

```bash
cd ~/tractive-ingestor
git pull
docker compose up -d --build
docker compose run --rm ingestor init-db   # jen pokud se změnilo schema.sql
```

Užitečné: `docker compose ps`, `docker compose restart ingestor`, `docker compose run --rm ingestor check`.
Data DB jsou ve volume `tractive_pgdata`, zachycené payloady ve `tractive_fixtures`.
Služby mají `restart: unless-stopped` → po rebootu Pi naběhnou samy (Docker je enabled v systemd).

---

## B) Bez Dockeru: venv + systemd

```bash
# 1) Systém + servisní uživatel bez shellu
sudo apt update && sudo apt install -y git python3 python3-venv
sudo useradd --system --home-dir /opt/tractive-ingestor --shell /usr/sbin/nologin tractive

# 2) Repo do /opt
sudo git clone https://github.com/martinp0/maja.git /opt/tractive-ingestor
cd /opt/tractive-ingestor

# 3) venv + balíček (bookworm má Python 3.11 – stačí)
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo .venv/bin/pip install .

# 4) Konfigurace – čitelná jen pro službu
sudo cp .env.example .env
sudo nano .env                     # TRACTIVE_*, DATABASE_URL, LOG_FORMAT=json
sudo mkdir -p fixtures             # jediný zapisovatelný adresář (ReadWritePaths)
sudo chown -R tractive:tractive /opt/tractive-ingestor
sudo chmod 600 .env

# 5) Schéma DB (jednou)
sudo -u tractive .venv/bin/python -m tractive_ingestor init-db

# 6) Služba
sudo cp deploy/tractive-ingestor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tractive-ingestor

# 7) Logy (journald)
journalctl -u tractive-ingestor -f
systemctl status tractive-ingestor
```

Aktualizace: `cd /opt/tractive-ingestor && sudo -u tractive git pull && sudo .venv/bin/pip install . && sudo chown -R tractive:tractive . && sudo systemctl restart tractive-ingestor`.

Unit má hardening (`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `NoNewPrivileges`, …); zapisovat smí jen do
`/opt/tractive-ingestor/fixtures`. Když `FIXTURES_DIR` přesuneš jinam, uprav `ReadWritePaths`.
Aby journald logy přežily reboot: `sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald`.

---

## Databáze

- **Lokálně (compose služba `db`)** – `postgis/postgis:16-3.4`, PostGIS je v image. Port se ven nepublikuje.
  U cesty B si buď nainstaluj Postgres + PostGIS na Pi (`sudo apt install postgresql postgis`), nebo použij Supabase.
- **Supabase** – stačí connection string do `DATABASE_URL` (cesta B) nebo `INGESTOR_DATABASE_URL` v `.env`
  a spouštět jen ingestor: `docker compose up -d --no-deps ingestor` (cesta A).
  Použij **Session pooler (5432)** nebo direct connection. PostGIS musí být zapnutý (*Database → Extensions → postgis*);
  `init-db` ho jinak zkusí zapnout sám přes `CREATE EXTENSION`.

## Healthcheck a monitoring

Ingestor má vestavěný HTTP health server (stdlib asyncio, žádná další závislost), který běží ve stejném procesu:

| Endpoint | Význam | 200 | 503 |
|---|---|---|---|
| `GET /healthz` | **liveness** – proces a event loop odpovídají | vždy, když proces žije | – (nedostupný = mrtvý/zaseklý) |
| `GET /readyz` | **readiness** – Tractive stream opravdu žije | přihlášeno **a** kanál připojený **a** event/keep-alive/handshake za posledních `HEALTH_READY_MAX_AGE_S` (90 s) | jinak; JSON `reasons` řekne proč |

Odpověď `/readyz` (zkráceně):
```json
{"status": "not_ready", "reasons": ["channel_stale"], "phase": "streaming",
 "authenticated": true, "channel_connected": true, "last_event_age_s": 812.4,
 "last_keepalive_age_s": 131.0, "reconnects": 3, "last_error": "channel disconnected: ..."}
```
`phase` = `starting | login | discovery | backfill | streaming | stopping` – během prvního backfillu je `/readyz` 503, to je v pořádku.

- **Docker:** `HEALTHCHECK` v Dockerfile volá `/healthz` (Python urllib, bez curl) → `docker compose ps` ukáže `(healthy)`.
  Záměrně ne `/readyz`: krátký výpadek Tractive nemá kontejner označit jako unhealthy a restartovat – reconnect řeší ingestor sám.
  Port je publikovaný jen na loopback Pi: `curl http://127.0.0.1:8080/readyz`.
- **systemd:** server poslouchá na `HEALTH_HOST:HEALTH_PORT` z `.env` (default `127.0.0.1:8080`). `WatchdogSec=` se nepoužívá
  (vyžadovalo by sd_notify); pád procesu řeší `Restart=always`.
- **Vzdálený monitoring přes Tailscale:** Docker → v `.env` `HEALTH_PUBLISH_ADDR=<tailscale ip -4>`, `docker compose up -d`;
  systemd → `HEALTH_HOST=<tailscale ip -4>` a v unitu odkomentovat `After=… tailscaled.service`. Pak z jiného stroje v tailnetu:
  `curl -fsS http://<pi>:8080/readyz` (např. Uptime Kuma „HTTP(s)“ monitor, alert na ≠ 200).
  Nebinduj `0.0.0.0` – endpoint by byl vidět v celé LAN (neobsahuje polohu, ale i tak).

## Appka (PWA + API, služba `api`)

```bash
cd ~/tractive-ingestor && git pull
echo "API_PUBLISH_ADDR=$(tailscale ip -4)" >> .env      # jen jednou; NIKDY 0.0.0.0 (API zatím bez přihlášení)
docker compose up -d --build api
curl -fsS http://$(tailscale ip -4):8090/api/ready
```

Na iPhonu v tailnetu otevři `http://<tailscale-ip>:8090/` → Sdílet → Přidat na plochu. Image staví i PWA (Node/Vite,
špička ~0,5 GB RAM). Kdyby build na Pi padal na paměť (OOM, `Killed`), postav `web/dist` na Macu
(`cd web && npm ci && npm run build`), zkopíruj na Pi (`scp -r web/dist <pi-host>:~/tractive-ingestor/web/`) a v
`docker-compose.yml` u služby `api` odkomentuj `additional_contexts: webdist: ./web/dist`. Detaily v hlavním README.

## Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up                  # přihlásit, Pi se objeví v tailnetu
tailscale ip -4
```

- Pi je pak dostupné odkudkoli (SSH, později API/dashboard) **bez otevírání portů** na routeru.
- Budoucí lokální HomeKit/HAP bridge poběží na stejném Pi – Tailscale řeší jen vzdálenou správu, HomeKit dál
  komunikuje lokálně. **Pi by mělo být ve stejné LAN jako domácí centrum Apple Home** (Apple TV / HomePod),
  protože HomeKit hledá zařízení přes mDNS/Bonjour, které přes Tailscale neprojde.

## Bezpečnost

- `.env` obsahuje **heslo k Tractive** (refresh token neexistuje, musí být uložené) → `chmod 600`, vlastník
  servisní uživatel (`tractive`) resp. tvůj uživatel u Dockeru. Nikdy necommitovat.
- Pro Tractive používej unikátní heslo.
- `fixtures/` a DB obsahují přesnou polohu psa = adresu domova. Port Postgresu nevystavuj do internetu.
