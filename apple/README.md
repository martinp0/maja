# Mája pro iPhone, widget a Apple Watch

Nativní SwiftUI appka nad stejným API jako PWA. **Jen čte**: kde pes je, doma / venku / utekla,
vzdálenost od domova (a volitelně ode mě), stáří polohy a baterie. Povely (Live, světlo, zvuk)
zůstávají v PWA.

| Target | Bundle ID | Co dělá |
|---|---|---|
| `Maja` (iOS 17) | `cz.martinpohl.maja` | Mapa s domovským kruhem, stav, baterie, „Navigovat“ v Mapách, nastavení |
| `MajaWidget` | `cz.martinpohl.maja.widget` | Widget na plochu (malý/střední) a na zamčenou obrazovku |
| `MajaWatch` (watchOS 10) | `cz.martinpohl.maja.watchkitapp` | Stav, vzdálenost, mini mapa |
| `MajaWatchWidget` | `cz.martinpohl.maja.watchkitapp.widget` | Komplikace (circular, rectangular, inline, corner) |

## Přihlášení: read-only token

Appka nepoužívá `APP_PIN` ani cookie. Na serveru nastav v `.env`:

```bash
API_READ_TOKENS=iphone=$(openssl rand -hex 32),watch=$(openssl rand -hex 32)
docker compose up -d api
```

Pořád jde i starý seznam bez jmen, oddělený čárkou. V logu je jen jméno (`iphone`, `watch`,
u nepojmenovaných `token-1`), nikdy samotný token.

Token projde jen `GET`/`HEAD` na `/api/pets`, `/api/trackers`, `/api/trackers/{id}/latest|positions|health`
a `/api/geofence/state|events`. Cokoli jiného vrátí 403. Špatný token je 401 a počítá se do
**vlastního** rate-limitu podle IP klienta (ne do limitu PINu). Platný token cizí neúspěchy nezamknou.
Trasa (`/positions`) je pro token jen posledních 48 h. `/api/geofence/state` neobsahuje souřadnice
domova – kruh na mapě se kreslí z volitelné šířky a délky v nastavení appky (jen na zařízení).
Bez `API_READ_TOKENS` se nic nemění.

V appce: ⚙︎ → adresa serveru (https, mimo LAN) + token → **Ověřit připojení** → **Uložit**.
Token se ukládá jen do Klíčenky (`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, sdílená skupina
s widgetem), nikdy do UserDefaults ani do repa. Na hodinky ho telefon pošle přes WatchConnectivity
a hodinky si ho uloží do vlastní Klíčenky. Zrušení přístupu = smaž token z `API_READ_TOKENS` a restartuj `api`.

## Sestavení

```bash
brew install xcodegen
cd apple && xcodegen generate && open Maja.xcodeproj
```

V Xcode u všech čtyř targetů nastav Team. Capabilities jsou už v `project.yml`:
App Groups `group.cz.martinpohl.maja` a Keychain Sharing `cz.martinpohl.maja.shared`.
Widget se obnovuje zhruba každých 15 minut (limit WidgetKitu). Appka v popředí každých 30 s.
