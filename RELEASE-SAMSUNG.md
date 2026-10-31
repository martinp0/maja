# Mája na Samsungu

Sideload na Galaxy (telefon, Z Flip, Z Fold) a mapa na televizi The Serif. Nic se sem samo nepublikuje na Galaxy Store ani na Google Play.

## Proč Trusted Web Activity, a ne Capacitor

Telefonní Mája už je PWA: mapa, přihlášení, povely, Web Push na geofence. Server má každý vlastník jiný, takže Bubblewrap s jednou adresou zapečenou při buildu nesedí.

Android appka (`android/`, `applicationId` `cz.martinpohl.maja`) je tenký obal:

1. **HTTPS a Chrome** otevře [Trusted Web Activity](https://developer.chrome.com/docs/android/trusted-web-activity). Stránka běží v Chrome, takže stávající Web Push (VAPID, service worker) hlásí odchod a návrat hned. `DelegationService` po ověření `assetlinks.json` ukáže ta upozornění jako Máju, ne jako Chrome.
2. **HTTP** (Tailscale `http://100…`) TWA neumí. Otevře se WebView ve stejné appce. Tam nejde Push API, zato WindowManager posílá do stránky polohu pantu, takže Flex Mode funguje i bez podpory v Chrome.
3. Volba **Vždy otevřít v aplikaci** na obrazovce Server udělá z HTTPS taky WebView, když chceš pant důležitější než okamžitý push.

Capacitor by stránku taky dal do WebView a Web Push by se musel přepsat na Firebase. To by sahalo na server kvůli věci, kterou prohlížeč už umí. Widget na kryt Flipu a záložní kontrola jednou za ~15 minut (WorkManager, read-only token) jsou vedle push, ne místo něj. Patnáct minut na útěk psa nestačí — na to je pořád vypínač upozornění ve webové Máje.

PWA v Chrome (Přidat na plochu) dál funguje. APK je navíc, ne náhrada.

Skládací rozložení je v CSS a v `web/src/foldable.ts`, takže ho má prohlížeč, TWA i WebView:

| Okno | Chování |
|---|---|
| Telefon, pop-up, rozdělené okno | Spodní list jako doteď |
| Z Fold na výšku (skoro čtverec, nebo svislý pant) | Mapa a detaily vedle sebe, obsah mimo pant |
| Z Flip v půlce (Flex Mode, vodorovný pant) | Mapa nahoře, ovládání dole |
| Kryt Flipu (malý čtverec na celý displej) | Náhled mapy, jméno, doma/venku, baterie |
| Od 900 px | Původní desktopový panel. iPad na výšku zůstává u listu |

Když Chrome segmenty viewportu nehlásí, Fold na výšku poznáme podle poměru stran. Půlku Flipu bez Device Posture API nebo nativního mostu poznat nejde — velikost okna se při ohnutí nemění. Na HTTPS to tedy chce Chrome, který segmenty umí (na skládacích Galaxy v roce 2026 ano). Na HTTP to řeší WebView.

Stav listu a otevřené nastavení se drží v `sessionStorage`, když složení stránku znovu načte. Aktivita ve WebView se při změně velikosti neničí (`configChanges`). Více oken a pop-up: `resizeableActivity` a metadata Samsung multi-window. Orientace není zamčená.

**Kryt Z Flip.** Widget (2×2, jde zmenšit) ukáže jméno, Doma/Venku, baterii a stáří polohy. **Bez souřadnic.** Na kryt ho přidáš v Nastavení → Krycí obrazovka → Widgety. Živou mapu na kryt Samsung třetím stranám jako widget nedá (jen RemoteViews). Celá appka na krytu jde, když v One UI zapneš pokračování aplikací na krycí obrazovce (název se liší podle verze: Laboratoř / Krycí obrazovka). Pak sedí rozložení „kryt“.

## Co musíš dodat ty

- Adresu serveru. **https** pro push a celou obrazovku, nebo **http** v tailnetu pro WebView.
- Vlastní keystore. V gitu není a nesmí být.
- `ANDROID_SHA256_CERT_FINGERPRINTS` v `.env` (otisk níže). Bez něj Chrome ukáže lištu s adresou, appka jinak funguje.
- `API_READ_TOKENS` (stejné jako iPhone), když chceš widget, záložní upozornění nebo televizi.
- Účet Samsung, až budeš chtít Galaxy Store. Teď se nikam neodesílá.

## Podpis a build

Na počítači JDK 17 a Android SDK (platform 35, build-tools 35). `ANDROID_HOME` musí ukazovat na SDK.

```bash
keytool -genkeypair -v -keystore "$HOME/maja-release.keystore" -alias maja \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -dname "CN=Martin Pohl, O=Maja, C=CZ"
# hesla si nech. Soubor necommituj.

export MAJA_KEYSTORE_PATH="$HOME/maja-release.keystore"
export MAJA_KEYSTORE_PASSWORD='…'
export MAJA_KEY_ALIAS=maja
export MAJA_KEY_PASSWORD='…'
export MAJA_VERSION_CODE=1
export MAJA_VERSION_NAME=0.1.0

cd android
chmod +x ./gradlew
./gradlew :app:assembleRelease :app:bundleRelease
```

Výstupy:

- `android/app/build/outputs/apk/release/app-release.apk` — sideload
- `android/app/build/outputs/bundle/release/app-release.aab` — Play, kdybys ho chtěl později

`assembleRelease` bez těch čtyř proměnných skončí chybou. Debug (`assembleDebug`) jde bez nich a má stejné `applicationId`, ať se otisk debug keystoru dá dát do `.env` vedle release.

Otisk do `.env` (není to tajemství, je v APK, ale je jiný pro každý keystore):

```bash
keytool -list -v -keystore "$MAJA_KEYSTORE_PATH" -alias "$MAJA_KEY_ALIAS"
# řádek SHA256, do .env může být s dvojtečkami i bez
```

```bash
ANDROID_SHA256_CERT_FINGERPRINTS=AA:BB:…
docker compose up -d api
```

Ověření: `curl -fsS https://tvůj-server/.well-known/assetlinks.json`  
Balíček v souboru je `cz.martinpohl.maja`. Chrome vztah ověří až po chvíli; do té doby může být vidět lišta.

V appce: podrž ikonu → **Server**. Vyplň adresu (jen origin, bez cesty). Token volitelně. Ulož.

## Sideload

Na telefonu: Nastavení → Zabezpečení a soukromí → Instalace neznámých aplikací → povolit pro Soubory nebo pro prohlížeč, kterým APK otevřeš. Pak APK otevři.

Z počítače, ladění USB zapnuté:

```bash
adb install -r android/app/build/outputs/apk/release/app-release.apk
```

Aktualizace: zvyš `MAJA_VERSION_CODE`, podepiš **stejným** keystorem, nainstaluj znovu. Jiný klíč Android jako aktualizaci nepřijme.

## Galaxy Store (nepublikováno)

1. Účet na [seller.samsungapps.com](https://seller.samsungapps.com). Soukromá osoba jde, firma chce D-U-N-S.
2. Nová aplikace, platforma Android, balíček `cz.martinpohl.maja`.
3. Nahraj **APK** (Galaxy Store AAB nepotřebuje). Stejný podpis jako sideload.
4. Vyplň texty níž (cs + en), ikonu 512, snímky z `docs/screenshots/samsung/`.
5. Věkové hodnocení a odkaz na zásady soukromí. Mája je self-hosted: telefon mluví jen s tvým serverem, polohu zařízení appka nesbírá, poloha psa je na tom serveru. Žádná reklama, žádné třetí strany kromě dlaždic OpenStreetMap.
6. Země distribuce klidně jen Česko. Cena 0. **Odeslat ke kontrole až budeš chtít.** Tenhle repozitář to nedělá.

## Google Play (nepublikováno)

1. [Play Console](https://play.google.com/console), jednorázový poplatek.
2. Aplikace, balíček `cz.martinpohl.maja`, nahraj **AAB**.
3. Data safety: appka nesbírá polohu zařízení. Data psa stahuje z serveru, který si uživatel nastaví. Žádné reklamy, žádný prodej dat.
4. Zásady soukromí (URL). Testovací stopa (internal testing) stačí pro tebe. Produkci nezveřejňuj, dokud o to nestojíš.

## Texty do obchodu

### Čeština

- **Název:** Mája
- **Krátký popis:** Kde je pes. Mapa, baterie a upozornění z tvého serveru.
- **Dlouhý popis:** Mája ukazuje polohu obojku na mapě, stav baterie a jestli je pes doma nebo venku. Připojuje se jen k serveru, který si sám provozuješ. Povely (živá poloha, zvuk, světlo) jsou v aplikaci po přihlášení. Na Z Flip a Z Fold se mapa přizpůsobí pantu. Na krytu Flipu je widget se jménem, doma/venku a baterií. Není to aplikace Tractive a Tractive za ni neručí.

### English

- **Name:** Mája
- **Short description:** Where the dog is. Map, battery and alerts from your own server.
- **Full description:** Mája shows the collar on a map, the battery, and whether the dog is home or out. It talks only to a server you run yourself. Commands (live tracking, sound, light) are in the app after you sign in. On Galaxy Z Flip and Z Fold the layout follows the hinge. The Flip cover screen can show a widget with the name, home or away, and the battery. This is not a Tractive app and Tractive does not support it.

## Televize (Tizen 6.5)

Dává smysl: v obýváku chceš vidět, kde pes je, ne na telefonu mačkat bzučák. The Serif z roku 2022 má Tizen 6.5 a webový engine kolem Chrome 85. Telefonní PWA (React 19, service worker, passkeys) na něm spolehlivě nepojede a ovladačem se špatně trefuješ do povelů. TV appka je proto **jen čtení**: velká mapa, jméno, doma/venku, baterie, stáří polohy. Šipky posouvají fokus, na mapě ji posouvají, Enter na mapě vycentruje psa. Token je stejný read-only jako u iPhonu. Souřadnice domova se do televize neukládají natrvalo jinak než tím, co vrátí API (kruh na mapě). Token zůstane v `localStorage` televize — je jen na čtení, stejně ho ber jako klíč k polohovému API a na serveru ho umíš smazat.

Sestavení (složka i `.wgt`):

```bash
chmod +x tizen/build.sh
./tizen/build.sh
```

Na TV zapni vývojářský režim (Aplikace → zadej 12345 na dálkovém ovladači, nebo IP vývojáře podle návodu Samsungu pro ten rok) a z počítače ve stejné síti:

```bash
aerotizen install tizen/package
# nebo
aerotizen install tizen/dist/Maja.wgt
```

`aerotizen` balíček podepíše a nainstaluje. Certifikát televize do gitu nedávej. První spuštění se zeptá na adresu serveru a token.

## CI

`.github/workflows/samsung.yml` na GitHubu staví debug i release APK, AAB (release podepíše jednorázový keystore v runneru, **není** to klíč pro telefon) a `Maja.wgt`. Jednorázový klíč z CI na telefon neinstaluj — při příští aktualizaci by neseděl. Svůj release keystore si nech ty.
