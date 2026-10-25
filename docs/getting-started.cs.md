# Spusť Máju lokálně

Mája je malý web pro jeden GPS obojek [Tractive](https://tractive.com).
Docker spustí tři části: Postgres (databáze), ingestor (jediný program, který mluví s Tractive) a web na portu 8090.

Návod platí pro Mac, Windows, Linux i 64bitové Raspberry Pi. Telefon s Tractive nemluví. Otevírá jen web, který běží u tebe.

English: [getting-started.md](getting-started.md).

## Co potřebuješ

- **Docker** s pluginem Compose.
  - Mac nebo Windows: nainstaluj [Docker Desktop](https://docs.docker.com/get-docker/) a nech ho běžet.
  - Linux nebo Raspberry Pi: Docker Engine a plugin Compose (`docker compose version` vypíše verzi). Pi musí být **64bitové** (`uname -m` vypíše `aarch64`). 32bitový systém tyhle image nespustí.
- **Účet Tractive** s **e-mailem a heslem** (ne přihlášení přes Apple nebo Google) a **aktivním předplatným**, pokud chceš živá data z obojku. Bez účtu jde nahrát fiktivní fenu. Viz [Vyzkoušej to bez účtu Tractive](#vyzkoušej-to-bez-účtu-tractive).
- Pár gigabajtů volného místa. První build stáhne Postgres, Python a Node.

## 1. Klonování

```bash
git clone https://github.com/martinp0/maja
cd maja
```

## 2. Soubor `.env`

```bash
cp .env.example .env
chmod 600 .env
```

`.env` zůstává jen u tebe. Necommituj ho. Otevři ho v libovolném editoru.

### Nutné pro opravdový obojek

| Proměnná | Co tam dát |
|---|---|
| `TRACTIVE_EMAIL` | E-mail účtu Tractive. |
| `TRACTIVE_PASSWORD` | Heslo k tomu účtu. Použij heslo, které máš jen na tohle. Přihlášení přes Apple nebo Google nefunguje. |
| `POSTGRES_PASSWORD` | Heslo databáze. V `.env.example` je `change-me-too`. Změň ho. Nepoužívej `@`, `:`, `/`, `?` ani `#` — heslo se skládá do URL. |

`POSTGRES_USER` a `POSTGRES_DB` můžou zůstat `tractive`.

### Nutné, než web otevře někdo další

| Proměnná | Co tam dát |
|---|---|
| `APP_PIN` | Dlouhá fráze. Prázdné = web **bez přihlášení**. Na vlastním notebooku to stačí. Ne když je port dostupný i jiným lidem. |
| `APP_SESSION_SECRET` | Klíč cookie. Vyrob ho přes `openssl rand -hex 32`. Když zůstane prázdný, po restartu API se všichni odhlásí. |

### Volitelné

| Proměnná | Prázdné znamená |
|---|---|
| `HOME_LAT`, `HOME_LON` | Žádný kruh domova a žádná historie odchodů. Jsou to souřadnice domova. Patří jen do `.env`. `seed-demo` umí vypsat bod **v parku** pro ukázkový kruh — to není dům. |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | Push vypnutý. |
| `WEBAUTHN_RP_ID`, `WEBAUTHN_ORIGIN` | Passkey použije `localhost` a `http://localhost:8090`. |
| `API_PUBLISH_ADDR` | Web jen na tomhle počítači: `127.0.0.1:8090`. |
| `API_READ_TOKENS` | Nativní appka pro iPhone a Watch (`apple/`) se nepřihlašuje. `jméno=token` nebo seznam oddělený čárkou, každý token z `openssl rand -hex 32`. Umí jen číst polohu, baterii a doma/venku, trasu jen za 48 h a nedostane souřadnice domova. |
| `API_PUBLISH_PORT` | Port `8090`. |
| `DEMO_MODE` | Ingestor se zkouší přihlásit k Tractive. `DEMO_MODE=1` přihlášení přeskočí. |
| `INGESTOR_DATABASE_URL` | Compose použije kontejner Postgres. Vyplň to jen pro Supabase a pak spouštěj samotný ingestor. |

Když `VAPID_SUBJECT` chybí, použije se `mailto:you@example.com`. Než se na push spolehneš, dej do `.env` vlastní `mailto:`.

### Passkeys: localhost, nebo vlastní doména

Passkey platí jen na adrese, kde vznikl.

- Na tomhle počítači proměnné nenastavuj. Compose použije `WEBAUTHN_RP_ID=localhost` a `WEBAUTHN_ORIGIN=http://localhost:8090`.
- Na vlastní doméně nastav obojí, bez cesty navíc a bez lomítka na konci:

  ```bash
  WEBAUTHN_RP_ID=tracker.example.com
  WEBAUTHN_ORIGIN=https://tracker.example.com
  ```

  `WEBAUTHN_RP_ID` je jen hostname (bez `https://` a bez portu). `WEBAUTHN_ORIGIN` je přesná adresa, kterou píšeš do prohlížeče. Víc originů může být oddělených čárkou; passkey stejně patří k jednomu RP ID.

### Klíče pro push

Až bude hotový první image (další krok), vygeneruj klíče a výstup vlož do `.env`:

```bash
docker compose run --rm --no-deps ingestor gen-vapid --subject mailto:you@example.com
```

Push zůstane vypnutý, dokud ty řádky v `.env` nejsou a kontejnery znovu nevytvoříš (`docker compose up -d`).

## 3. Start

```bash
docker compose up -d --build
```

Jeden příkaz sestaví ingestor, sestaví web (`web/` se kompiluje uvnitř image API) a spustí Postgres. `npm` nespouštíš a předem postavený `web/dist` nepotřebuješ.

První build trvá několik minut. Další starty jsou rychlejší.

Na Raspberry Pi s asi 2 GB RAM může build Node skončit nedostatkem paměti. Pak `web/dist` postav jinde (`cd web && npm ci && npm run build`), složku `web/dist` zkopíruj na Pi a u služby `api` v `docker-compose.yml` odkomentuj:

```yaml
additional_contexts:
  webdist: ./web/dist
```

Blok v souboru nech. Na běžném počítači ať zůstane zakomentovaný, ať Compose web staví sám.

## 4. Tabulky

```bash
docker compose run --rm ingestor init-db
```

Jde spustit znovu. Doplní jen chybějící tabulky.

## 5. Otevři web

Na stejném počítači otevři <http://localhost:8090>.

- Prázdný `APP_PIN`: rovnou mapa.
- Vyplněný `APP_PIN`: nejdřív obrazovka s frází. Napiš ji.

Adresa je jen `127.0.0.1`, takže telefon ve stejné Wi-Fi se tam zatím nedostane. Je to schválně. Viz [Vlastní doména nebo tailnet](#vlastní-doména-nebo-tailnet).

### Kdy se objeví první body

S opravdovým obojkem sleduj ingestor:

```bash
docker compose logs -f ingestor
```

Má projít přihlášení, doplnění historie (`BACKFILL_DAYS`, výchozí 7 dní) a fáze streamu. Pak obnov stránku. První backfill může trvat pár minut. Obojek doma na Wi-Fi v úsporném režimu posílá body málokdy; mapa může být tichá, i když přihlášení prošlo.

### Heslo z příkladu se nepřihlásí

`.env.example` má `TRACTIVE_EMAIL=you@example.com` a `TRACTIVE_PASSWORD=change-me`. Tractive je odmítne. Ingestor skončí a Docker ho znovu spustí. **Web i `/api/health` přesto jedou.** Mapa zůstane prázdná, dokud neprojde opravdové přihlášení, nebo dokud nenahraješ ukázkovou fenu.

`docker compose up -d --build` v tom stavu pořád dopadne dobře. V `docker compose ps` mají být `db` a `api` healthy. `ingestor` může být `Restarting`. To je špatné heslo, ne rozbitá instalace.

## Vyzkoušej to bez účtu Tractive

Vloží se jedna fiktivní fena. Tractive se nevolá a nepoužije se adresa domova.

Do `.env`:

```bash
DEMO_MODE=1
```

Ukázkový e-mail tam může zůstat. Demo režim ho nikam neposílá.

```bash
docker compose up -d --build
docker compose run --rm ingestor seed-demo
```

`seed-demo` případně založí tabulky a přepíše jen ukázkovou fenu (obojek `DEMO-LUNA`, jméno Luna). Ostatní obojky v téže databázi nechá být. Příkaz můžeš spustit znovu, ať je procházka zase v posledních 24 hodinách.

Příkaz vypíše `HOME_LAT` a `HOME_LON`. Jsou to souřadnice fontány Bethesda Fountain v Central Parku v New Yorku. Vlož je do `.env` jen když chceš čárkovaný kruh a seznam odchodů. Pak znovu vytvoř API, ať si je přečte:

```bash
docker compose up -d api
```

`HOME_LAT` a `HOME_LON` nech prázdné, když zónu nechceš. Až přejdeš na opravdový obojek, `DEMO_MODE` smaž (nebo dej `0`) a do `.env` případně dej **svůj** domov. Ty řádky necommituj.

Ukázkovou fenu smažeš, aniž sáhneš na cokoliv jiného:

```bash
docker compose run --rm ingestor seed-demo --clear
```

## Logy a health

```bash
docker compose ps
docker compose logs -f --tail=100 ingestor
docker compose logs -f --tail=100 api
curl -fsS http://127.0.0.1:8090/api/health
curl -fsS http://127.0.0.1:8090/api/ready
curl -fsS http://127.0.0.1:8080/healthz
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/readyz
```

| Kontrola | V pořádku, když |
|---|---|
| `docker compose ps` → `api` healthy | Proces webu odpovídá na `/api/health`. |
| `GET /api/health` je 200 | API běží. Nedívá se do databáze ani na Tractive. |
| `GET /api/ready` je 200 | API umí dotaz do Postgresu. |
| `GET /healthz` na portu 8080 je 200 | Proces ingestoru běží. Špatné heslo k Tractive takhle vypadá, dokud proces neskončí. |
| `GET /readyz` na portu 8080 je 200 | Přihlášeno k Tractive a živý kanál je čerstvý. **503 je normální** během prvního backfillu, v demo režimu, nebo když je heslo špatně. |

Port 8080 je ingestor, jen na tomhle počítači. Port 8090 je web.

## Aktualizace

```bash
cd maja
git pull
docker compose up -d --build
docker compose run --rm ingestor init-db
```

`init-db` je v pořádku i když se schéma nezměnilo.

## Zastavení

```bash
docker compose stop          # pauza; data zůstanou
docker compose down          # kontejnery pryč; volume databáze zůstane
docker compose down -v       # smaže i volume databáze
```

Kontejnery se samy nastartují, dokud je nezastavíš, a po rebootu taky, pokud se Docker spouští se systémem.

## Záloha databáze

Databáze je ve volume `tractive_pgdata` (projekt `tractive`, volume `pgdata`).

Logický dump je ten, který si schovej:

```bash
docker compose exec -T db pg_dump -U tractive -d tractive --no-owner > maja-backup.sql
```

Když jsi změnil `POSTGRES_USER` nebo `POSTGRES_DB`, použij ty názvy.

Obnova do běžící databáze (nejdřív zastav web, ať se při obnově nezapisuje):

```bash
docker compose stop api ingestor
docker compose exec -T db psql -U tractive -d tractive < maja-backup.sql
docker compose start api ingestor
```

Druhá možnost je kopie souborů volume. Postgres předtím zastav, ať jsou soubory konzistentní:

```bash
docker compose stop db
docker run --rm -v tractive_pgdata:/v -v "$PWD":/backup alpine \
  tar czf /backup/pgdata.tgz -C /v .
docker compose start db
```

Body v databázi jsou poloha psa. Zálohu drž v soukromí.

## Vlastní doména nebo tailnet

Když se na web zvenku dostane tunel, nech `API_PUBLISH_ADDR` na `127.0.0.1`. Předtím nastav `APP_PIN` a `APP_SESSION_SECRET`. Na `0.0.0.0` to binduj jen s nastavenou frází.

### Cloudflare Tunnel

`cloudflared` nainstaluj podle dokumentace Cloudflare. Jednorázová veřejná adresa (při každém spuštění jiná):

```bash
cloudflared tunnel --url http://127.0.0.1:8090
```

Pro vlastní hostname založ pojmenovaný tunel v Zero Trust a veřejný hostname namiř na `http://127.0.0.1:8090`. Pak nastav `WEBAUTHN_RP_ID` a `WEBAUTHN_ORIGIN` na ten hostname, jak je v [Passkeys: localhost, nebo vlastní doména](#passkeys-localhost-nebo-vlastní-doména).

### Tailscale

Nainstaluj Tailscale, spusť `tailscale up` a na tomhle stroji:

```bash
tailscale ip -4
```

Adresu `100.x.y.z` dej do `.env` jako `API_PUBLISH_ADDR` a spusť `docker compose up -d`. Jiný stroj ve stejném tailnetu otevře `http://100.x.y.z:8090`. Port na routeru se neotevírá.

Passkey na tom holém `http://100.x…` nefunguje. Potřebuješ HTTPS (HTTPS jméno Tailscale, nebo vlastní doménu) a `WEBAUTHN_*` nastavené na přesný origin. Passkey založený na `localhost` tu adresu neodemkne.

## Instalace na telefon

Web je PWA.

- Android (Chrome): menu → **Nainstalovat aplikaci** nebo **Přidat na plochu**.
- iPhone (Safari): Sdílet → **Přidat na plochu**.

Push na iOS (16.4 a novější) chodí jen na ikonu přidanou na plochu a jen přes HTTPS. Telefon neumí použít `http://localhost` z notebooku.

## HTTPS, passkeys a push

| | `http://localhost` | `http://` a IP nebo jméno v tailnetu | `https://` vlastní doména |
|---|---|---|---|
| Mapa, přihlášení, povely | Ano | Ano, když je tam port publikovaný | Ano |
| Passkeys (Face ID / Touch ID) | Ano | Ne | Ano, když `WEBAUTHN_*` sedí na ten web |
| Web Push | Prohlížeče ho na localhostu povolí | Většinou ne | Ano, na iOS jen z ikony na ploše |

Service worker je součást nainstalované appky. Chce zabezpečenou stránku: localhost, nebo HTTPS.

## Když něco nejde

### Přihlášení k Tractive selže

- Účet musí mít **vlastní e-mail a heslo**. Přihlášení přes Apple nebo Google sem nejde použít. V aplikaci Tractive nastav heslo, nebo psa nasdílej na e-mailový účet, který ovládáš.
- Předplatné musí být aktivní.
- `docker compose logs ingestor` ukáže odmítnutí. Proces skončí kódem 3 a Docker ho restartuje. `db` a `api` přitom můžou být healthy.
- `DEMO_MODE=1` přihlášení přeskakuje schválně. Vypni ho, až budeš chtít opravdový obojek.
- `GET /readyz` zůstane 503, dokud není v pořádku přihlášení i živý kanál.

### Žádné polohy

- Spusť `docker compose run --rm ingestor init-db`, pokud jsi to ještě neudělal.
- S heslem z příkladu nic nedorazí. Dej opravdový účet, nebo `seed-demo`.
- `DEMO_MODE=1` bez `seed-demo` je prázdná mapa schválně.
- Backfill může ještě běžet. Sleduj `docker compose logs -f ingestor`.
- Mapa bere posledních 24 hodin. `seed-demo` přepíše Luninu procházku vůči „teď“; když demo den stojí, spusť ho znovu.
- Obojek v úsporném režimu doma může dlouho neposlat nový GPS bod.

### Port už něco používá

8090 nebo 8080 drží jiný program. Zastav ho, nebo v `.env` nastav `API_PUBLISH_PORT` / `HEALTH_PUBLISH_PORT` a znovu `docker compose up -d`. Adresa se změní s portem (`http://localhost:8091`, když zvolíš 8091).

### ARM a x86

Oficiální image Pythonu a Node, které se tu používají, existují pro `linux/amd64` i `linux/arm64`. Docker Desktop na Apple Silicon a 64bitové Pi jsou arm64. Běžné PC je amd64. Compose staví pro stroj, na kterém právě jsi.

`uname -m` na Pi musí být `aarch64`. Když kontejner hned skončí s `exec format error`, image je pro druhou architekturu. Spusť `docker compose build --no-cache` na stroji, kde to má běžet.

Volitelná cesta `additional_contexts: webdist` je na každé architektuře stejná. Ten klíč ani službu `api` nepřejmenovávej. Pi, které už má odkomentované `webdist` a `WEBAUTHN_*` v `.env`, pojede dál.

### Passkeys na http nefungují

Face ID potřebuje zabezpečený kontext.

- `http://localhost:8090` a `http://127.0.0.1:8090` fungují, když je `WEBAUTHN_RP_ID=localhost` a `WEBAUTHN_ORIGIN` je přesně ta adresa (včetně portu).
- `http://192.168.x.x` a `http://100.x.y.z` ne. Dej web za HTTPS a `WEBAUTHN_RP_ID` / `WEBAUTHN_ORIGIN` nastav na adresu, kterou opravdu otevíráš.
- Passkey založený na localhostu se na vlastní doméně neukáže. Založ ho tam znovu.

### Web se nenačte

- Docker Desktop běží.
- `docker compose ps` ukazuje `api` jako up. Když chybí, `docker compose logs api`.
- Jsi na stejném stroji a používáš `http://localhost:8090`, pokud jsi neměnil `API_PUBLISH_ADDR`.
- `curl -fsS http://127.0.0.1:8090/api/health` vypíše JSON. Když curl funguje a prohlížeč ne, zkus anonymní okno (starý service worker umí držet rozbitou stránku).

## Snímky obrazovky

Ukázková fena Luna, procházka kolem Bethesda Fountain v Central Parku v New Yorku. Veřejný park, ne domov a ne data z Tractive.

![Telefon, světlá mapa](screenshots/map-light.webp)

![Telefon, tmavá mapa](screenshots/map-dark.webp)

![Počítač, panel vedle mapy](screenshots/desktop.webp)

![Historie odchodů a návratů](screenshots/history.webp)

![Nastavení](screenshots/settings.webp)

![Přihlášení frází](screenshots/login.webp)
