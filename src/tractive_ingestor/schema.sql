-- =============================================================================
-- Tractive ingestor – datový model (Postgres 14+ / Supabase)
--
-- VYŽADUJE PostGIS:  CREATE EXTENSION postgis;
--   Supabase: Database -> Extensions -> postgis (schéma "extensions"), nebo
--   spustit níže uvedený CREATE EXTENSION.
--
-- Skript je idempotentní (IF NOT EXISTS) – lze spustit opakovaně:
--   psql "$DATABASE_URL" -f src/tractive_ingestor/schema.sql
--   nebo: python -m tractive_ingestor init-db
--
-- Sociální vrstva (owner, dog, swipe, match, chat, place, presence…) sem
-- záměrně NEPATŘÍ – přijde později jako samostatná migrace (viz koncept kap. 5).
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid() na starším Postgresu

-- ----------------------------------------------------------------------------- číselníky
DO $$ BEGIN
    CREATE TYPE zone_kind AS ENUM ('home','park','forbidden','escape','custom');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ----------------------------------------------------------------------------- trackery a psi
-- owner_id = Supabase auth.users.id. Bez FK, aby schéma běželo i na čistém Postgresu.
CREATE TABLE IF NOT EXISTS trackers (
    tracker_id            text PRIMARY KEY,          -- Tractive _id / hw_id trackeru
    owner_id              uuid,
    model_number          text,                      -- např. TG6C
    fw_version            text,                      -- např. 011.092-TRV6-009-e7ce01
    hw_edition            text,
    capabilities          text[],                    -- LT, BUZZER, LED, BLE, WIFI_ZONE, SLEEP_TRACKING, BARK_DETECTION…
    state                 text,                      -- OPERATIONAL…
    state_reason          text,                      -- POWER_SAVING…
    charging_state        text,                      -- NOT_CHARGING / CHARGING…
    battery_state         text,                      -- REGULAR…
    power_saving_zone_id  text,
    last_switches         jsonb NOT NULL DEFAULT '{}'::jsonb, -- poslední live_tracking/buzzer/led stav ze streamu
    raw                   jsonb,                     -- poslední tracker details
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);
-- migrace starších instalací (skelet měl jen model/raw): model -> model_number
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = 'trackers' AND column_name = 'model')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = 'trackers' AND column_name = 'model_number') THEN
        ALTER TABLE trackers RENAME COLUMN model TO model_number;
    END IF;
END $$;
ALTER TABLE trackers ADD COLUMN IF NOT EXISTS model_number text;
ALTER TABLE trackers ADD COLUMN IF NOT EXISTS fw_version text;
ALTER TABLE trackers ADD COLUMN IF NOT EXISTS hw_edition text;
ALTER TABLE trackers ADD COLUMN IF NOT EXISTS capabilities text[];
ALTER TABLE trackers ADD COLUMN IF NOT EXISTS state text;
ALTER TABLE trackers ADD COLUMN IF NOT EXISTS state_reason text;
ALTER TABLE trackers ADD COLUMN IF NOT EXISTS charging_state text;
ALTER TABLE trackers ADD COLUMN IF NOT EXISTS battery_state text;
ALTER TABLE trackers ADD COLUMN IF NOT EXISTS power_saving_zone_id text;

CREATE TABLE IF NOT EXISTS pets (
    pet_id                     text PRIMARY KEY,     -- Tractive trackable_object _id
    tracker_id                 text REFERENCES trackers(tracker_id) ON DELETE SET NULL,
    owner_id                   uuid,
    name                       text,
    pet_type                   text,                 -- DOG / CAT
    breed_ids                  text[],
    gender                     text,                 -- F / M
    birthday                   date,
    weight_g                   integer,              -- details.weight je v gramech
    neutered                   boolean,
    home_location              geometry(Point, 4326),-- POZOR: adresa domova, citlivé
    daily_active_minutes_goal  integer,
    raw                        jsonb,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE pets ADD COLUMN IF NOT EXISTS pet_type text;
ALTER TABLE pets ADD COLUMN IF NOT EXISTS breed_ids text[];
ALTER TABLE pets ADD COLUMN IF NOT EXISTS gender text;
ALTER TABLE pets ADD COLUMN IF NOT EXISTS birthday date;
ALTER TABLE pets ADD COLUMN IF NOT EXISTS weight_g integer;
ALTER TABLE pets ADD COLUMN IF NOT EXISTS neutered boolean;
ALTER TABLE pets ADD COLUMN IF NOT EXISTS home_location geometry(Point, 4326);
ALTER TABLE pets ADD COLUMN IF NOT EXISTS daily_active_minutes_goal integer;

-- ----------------------------------------------------------------------------- raw události
-- Append-only. Vše, co přijde z Tractive (stream i REST), se uloží 1:1.
-- Umožňuje reprocess normalizovaných tabulek po změně API / opravě parseru.
CREATE TABLE IF NOT EXISTS events (
    id            bigserial PRIMARY KEY,
    received_at   timestamptz NOT NULL DEFAULT now(),
    source        text NOT NULL,                     -- 'channel' | 'pos_report' | 'hw_report' | 'history' | 'health' | 'command'
    event_type    text NOT NULL,                     -- stream "message" nebo REST endpoint
    tracker_id    text,
    pet_id        text,
    payload       jsonb NOT NULL,
    payload_hash  text NOT NULL,                     -- sha256 kanonického JSONu -> idempotence
    CONSTRAINT events_payload_hash_uq UNIQUE (payload_hash)
);
CREATE INDEX IF NOT EXISTS events_received_at_idx ON events (received_at DESC);
CREATE INDEX IF NOT EXISTS events_tracker_type_idx ON events (tracker_id, event_type, received_at DESC);

-- ----------------------------------------------------------------------------- polohy
CREATE TABLE IF NOT EXISTS positions (
    id                    bigserial PRIMARY KEY,
    tracker_id            text NOT NULL,
    ts                    timestamptz NOT NULL,      -- čas fixu z trackeru (`time`, unix s), ne čas přijetí
    geom                  geometry(Point, 4326) NOT NULL, -- lon/lat WGS84
    accuracy_m            real,                      -- pos_uncertainty
    speed                 real,                      -- často NULL (doma/WiFi); TODO: jednotka
    altitude_m            real,                      -- altitude (m n. m.)
    sensor_used           text,                      -- volný string: KNOWN_WIFI, GPS, BLE…
    power_saving_zone_id  text,                      -- vyplněno, když je pes v úsporné (domácí) zóně
    origin                text NOT NULL,             -- 'channel' | 'pos_report' | 'history'
    raw                   jsonb,                     -- zdrojový bod/report
    inserted_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT positions_tracker_ts_uq UNIQUE (tracker_id, ts)  -- idempotentní upsert
);
-- migrace: skelet měl sloupec `source` -> přejmenovat na `sensor_used`
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = 'positions' AND column_name = 'source')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = 'positions' AND column_name = 'sensor_used') THEN
        ALTER TABLE positions RENAME COLUMN source TO sensor_used;
    END IF;
END $$;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS sensor_used text;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS power_saving_zone_id text;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS raw jsonb;
CREATE INDEX IF NOT EXISTS positions_ts_idx ON positions (ts DESC);
CREATE INDEX IF NOT EXISTS positions_geom_gist ON positions USING gist (geom);

-- ----------------------------------------------------------------------------- hardware / baterie
CREATE TABLE IF NOT EXISTS hw_reports (
    id                    bigserial PRIMARY KEY,
    tracker_id            text NOT NULL,
    ts                    timestamptz NOT NULL,      -- device_hw_report.time (unix s)
    battery_pct           smallint CHECK (battery_pct BETWEEN 0 AND 100), -- battery_level
    charging              boolean,                   -- odvozeno z tracker.charging_state
    charging_state        text,                      -- tracker.charging_state (NOT_CHARGING…)
    battery_state         text,                      -- tracker.battery_state (REGULAR…)
    tracker_state         text,                      -- tracker.state (OPERATIONAL…)
    power_saving          boolean,                   -- tracker.state_reason = POWER_SAVING
    power_saving_zone_id  text,
    clip_mounted_state    text,
    temperature_state     text,
    hw_status             text,
    report_id             text,
    origin                text NOT NULL,
    raw                   jsonb,
    inserted_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT hw_reports_tracker_ts_uq UNIQUE (tracker_id, ts)
);
-- firmware v hw_reportu není (je v trackers.fw_version) -> sloupec ze skeletu pryč
ALTER TABLE hw_reports DROP COLUMN IF EXISTS firmware;
ALTER TABLE hw_reports ADD COLUMN IF NOT EXISTS charging_state text;
ALTER TABLE hw_reports ADD COLUMN IF NOT EXISTS battery_state text;
ALTER TABLE hw_reports ADD COLUMN IF NOT EXISTS clip_mounted_state text;
ALTER TABLE hw_reports ADD COLUMN IF NOT EXISTS temperature_state text;
ALTER TABLE hw_reports ADD COLUMN IF NOT EXISTS hw_status text;
ALTER TABLE hw_reports ADD COLUMN IF NOT EXISTS report_id text;
ALTER TABLE hw_reports ADD COLUMN IF NOT EXISTS raw jsonb;
CREATE INDEX IF NOT EXISTS hw_reports_ts_idx ON hw_reports (tracker_id, ts DESC);

-- ----------------------------------------------------------------------------- aktivita a spánek
-- Jeden řádek na psa a den; upsert přepisuje průběžnými hodnotami dne.
-- Zdroj: aps-api health overview (camelCase). `day` = datum activityDataSyncedAt v LOCAL_TZ.
-- TODO(fáze 0): potvrdit přiřazení ke dni (sync po půlnoci, dayOffset u jednotlivých metrik).
CREATE TABLE IF NOT EXISTS health_daily (
    pet_id               text NOT NULL,
    day                  date NOT NULL,              -- lokální den (LOCAL_TZ)
    synced_at            timestamptz,                -- activityDataSyncedAt
    minutes_active       integer,                    -- activity.minutesActive
    minutes_goal         integer,                    -- activity.minutesGoal
    hourly_distribution  integer[],                  -- activity.hourlyDistribution (24 hodnot)
    sleep_day_min        integer,                    -- sleep.minutesDaySleep
    sleep_night_min      integer,                    -- sleep.minutesNightSleep
    sleep_calm_min       integer,                    -- sleep.minutesCalm
    resting_hr_status    text,                       -- restingHeartRate.status (NORMAL…)
    resting_rr_status    text,                       -- restingRespiratoryRate.status
    bark_status          text,                       -- bark.status
    scratch_status       text,                       -- scratch.status (INFREQUENT…)
    raw                  jsonb,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pet_id, day)
);
-- migrace ze skeletu: přejmenovat staré sloupce spánku (zachová data), doplnit nové
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT * FROM (VALUES ('minutes_day_sleep','sleep_day_min'),
                                   ('minutes_night_sleep','sleep_night_min'),
                                   ('minutes_calm','sleep_calm_min')) AS m(old_name, new_name)
    LOOP
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = 'health_daily' AND column_name = r.old_name)
           AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = 'health_daily' AND column_name = r.new_name) THEN
            EXECUTE format('ALTER TABLE health_daily RENAME COLUMN %I TO %I', r.old_name, r.new_name);
        END IF;
    END LOOP;
END $$;
ALTER TABLE health_daily DROP COLUMN IF EXISTS minutes_day_sleep;
ALTER TABLE health_daily DROP COLUMN IF EXISTS minutes_night_sleep;
ALTER TABLE health_daily DROP COLUMN IF EXISTS minutes_calm;
ALTER TABLE health_daily ADD COLUMN IF NOT EXISTS synced_at timestamptz;
ALTER TABLE health_daily ADD COLUMN IF NOT EXISTS hourly_distribution integer[];
ALTER TABLE health_daily ADD COLUMN IF NOT EXISTS sleep_day_min integer;
ALTER TABLE health_daily ADD COLUMN IF NOT EXISTS sleep_night_min integer;
ALTER TABLE health_daily ADD COLUMN IF NOT EXISTS sleep_calm_min integer;
ALTER TABLE health_daily ADD COLUMN IF NOT EXISTS resting_hr_status text;
ALTER TABLE health_daily ADD COLUMN IF NOT EXISTS resting_rr_status text;
ALTER TABLE health_daily ADD COLUMN IF NOT EXISTS bark_status text;
ALTER TABLE health_daily ADD COLUMN IF NOT EXISTS scratch_status text;

-- ----------------------------------------------------------------------------- zóny (geofence)
-- Kruh = geom typu Point + radius_m; polygon = geom typu Polygon (radius_m NULL).
CREATE TABLE IF NOT EXISTS zones (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        uuid,
    tracker_id      text,                            -- NULL = platí pro všechny trackery vlastníka
    name            text NOT NULL,
    kind            zone_kind NOT NULL DEFAULT 'custom',
    geom            geometry(Geometry, 4326) NOT NULL,
    radius_m        real CHECK (radius_m IS NULL OR radius_m > 0),
    hysteresis_m    real NOT NULL DEFAULT 30,        -- proti GPS driftu
    min_accuracy_m  real NOT NULL DEFAULT 100,       -- horší fix se pro zóny ignoruje
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS zones_geom_gist ON zones USING gist (geom);

CREATE TABLE IF NOT EXISTS zone_events (
    id           bigserial PRIMARY KEY,
    zone_id      uuid NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    tracker_id   text NOT NULL,
    ts           timestamptz NOT NULL,
    transition   text NOT NULL CHECK (transition IN ('enter','exit')),
    position_id  bigint REFERENCES positions(id) ON DELETE SET NULL,
    notified_at  timestamptz,
    CONSTRAINT zone_events_uq UNIQUE (zone_id, tracker_id, ts, transition)
);
CREATE INDEX IF NOT EXISTS zone_events_tracker_ts_idx ON zone_events (tracker_id, ts DESC);

-- ----------------------------------------------------------------------------- geofence doma / venku
-- Jedna domácí zóna z env (HOME_LAT/HOME_LON/HOME_RADIUS_M – souřadnice NEJSOU v DB ani v gitu).
-- Logika: src/tractive_ingestor/home_geofence.py. Čistě aditivní – jen nové tabulky.
CREATE TABLE IF NOT EXISTS geofence_events (
    id           bigserial PRIMARY KEY,
    tracker_id   text NOT NULL,
    event_type   text NOT NULL CHECK (event_type IN ('left_home','returned_home')),
    happened_at  timestamptz NOT NULL,               -- ts prvního bodu na nové straně (začátek běhu)
    distance_m   real,                               -- vzdálenost od domova v tom bodě
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT geofence_events_uq UNIQUE (tracker_id, event_type, happened_at)  -- idempotentní zápis
);
CREATE INDEX IF NOT EXISTS geofence_events_tracker_idx ON geofence_events (tracker_id, happened_at DESC);

-- Checkpoint vyhodnocení (1 řádek na tracker): aktuální stav + rozběhnutý debounce.
CREATE TABLE IF NOT EXISTS geofence_state (
    tracker_id          text PRIMARY KEY,
    state               text NOT NULL CHECK (state IN ('home','away','unknown')),
    since               timestamptz,                 -- od kdy platí `state`
    last_ts             timestamptz,                 -- poslední zpracovaný bod z positions
    pending_state       text CHECK (pending_state IS NULL OR pending_state IN ('home','away')),
    pending_since       timestamptz,
    pending_distance_m  real,
    last_distance_m     real,
    evaluated_at        timestamptz NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------- push notifikace (Web Push / VAPID)
-- Logika: src/tractive_ingestor/push.py. Klíče VAPID jen v .env.
-- geofence_events.notified_at: událost už byla (nebo záměrně nebude) poslaná -> restart nic nepošle znovu.
-- Při PRVNÍM přidání sloupce se všechny dosavadní události označí jako odbavené (žádná lavina starých pushů).
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = 'geofence_events'
                     AND column_name = 'notified_at') THEN
        ALTER TABLE geofence_events ADD COLUMN notified_at timestamptz;
        UPDATE geofence_events SET notified_at = created_at;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS geofence_events_unnotified_idx ON geofence_events (happened_at) WHERE notified_at IS NULL;

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          bigserial PRIMARY KEY,
    endpoint    text NOT NULL,
    p256dh      text NOT NULL,
    auth        text NOT NULL,
    user_agent  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    last_ok_at  timestamptz,
    failures    integer NOT NULL DEFAULT 0,
    CONSTRAINT push_subscriptions_endpoint_uq UNIQUE (endpoint)
);

-- Jednorázová upozornění mimo geofence_events (1 řádek na tracker).
CREATE TABLE IF NOT EXISTS notify_state (
    tracker_id      text PRIMARY KEY,
    escape_since    timestamptz,   -- geofence_state.since away epizody, pro kterou už šlo „utekla“
    battery_low_at  timestamptz,   -- kdy šlo „slabá baterie“; NULL = znovu povoleno (baterie > 30 %)
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------- passkeys (WebAuthn / Face ID)
-- Přihlášení do appky bez hesla. Veřejné klíče (žádné tajemství); logika v src/tractive_api/passkeys.py.
CREATE TABLE IF NOT EXISTS webauthn_credentials (
    id             bigserial PRIMARY KEY,
    credential_id  bytea NOT NULL,
    public_key     bytea NOT NULL,                   -- COSE klíč
    sign_count     bigint NOT NULL DEFAULT 0,
    transports     text[],
    name           text,                             -- „iPhone“, „Mac“… (z User-Agentu)
    aaguid         text,
    backed_up      boolean,                          -- synchronizovaný passkey (iCloud Klíčenka)
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_used_at   timestamptz,
    CONSTRAINT webauthn_credentials_id_uq UNIQUE (credential_id)
);

-- ----------------------------------------------------------------------------- příkazy
-- Fronta povelů (LIVE / buzzer / LED), zpracovává ji commands.CommandWorker.
-- Stavový automat:
--   queued -> sending -> sent (API přijalo) -> done (potvrzeno streamem / bez potvrzení po timeoutu)
--   queued|deferred -> deferred (pes doma / power-saving / chyba API, zkusí se znovu) -> failed
-- `status` je text + CHECK (ne enum): ALTER TYPE ... ADD VALUE nejde použít ve stejné
-- transakci, ve které se hodnota přidává, a init-db pouští celý soubor najednou.
CREATE TABLE IF NOT EXISTS commands (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tracker_id       text NOT NULL,
    command          text NOT NULL CHECK (command IN ('live_tracking','buzzer_control','led_control')),
    action           text NOT NULL CHECK (action IN ('on','off')),
    status           text NOT NULL DEFAULT 'queued',
    requested_by     uuid,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    next_attempt_at  timestamptz,                    -- NULL = hned; deferred -> kdy zkusit znovu
    attempts         integer NOT NULL DEFAULT 0,     -- skutečná volání API (odklad kvůli zóně se nepočítá)
    sent_at          timestamptz,
    finished_at      timestamptz,
    response         jsonb,
    last_error       text
);
-- migrace skeletu: enum command_status -> text, error -> last_error, nové sloupce
DROP INDEX IF EXISTS commands_queue_idx;  -- predikát nad enumem by blokoval změnu typu
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = 'commands'
                 AND column_name = 'status' AND udt_name = 'command_status') THEN
        ALTER TABLE commands ALTER COLUMN status DROP DEFAULT;
        ALTER TABLE commands ALTER COLUMN status TYPE text USING (
            CASE status::text WHEN 'pending' THEN 'sent' WHEN 'confirmed' THEN 'done'
                              WHEN 'timeout' THEN 'failed' ELSE status::text END);
        ALTER TABLE commands ALTER COLUMN status SET DEFAULT 'queued';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = 'commands' AND column_name = 'error')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = 'commands' AND column_name = 'last_error') THEN
        ALTER TABLE commands RENAME COLUMN error TO last_error;
    END IF;
END $$;
ALTER TABLE commands ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE commands ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz;
ALTER TABLE commands ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;
ALTER TABLE commands ADD COLUMN IF NOT EXISTS last_error text;
DO $$ BEGIN
    ALTER TABLE commands ADD CONSTRAINT commands_status_chk
        CHECK (status IN ('queued','sending','sent','done','deferred','failed'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS commands_open_idx ON commands (created_at)
    WHERE status IN ('queued','deferred','sending','sent');
-- starý enum už nic nepoužívá
DROP TYPE IF EXISTS command_status;

-- ----------------------------------------------------------------------------- sdílení polohy
-- Časově omezený read-only odkaz (sitter, rodina, walk date). Ukládá se jen hash tokenu.
CREATE TABLE IF NOT EXISTS share_links (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tracker_id  text NOT NULL,
    created_by  uuid,
    token_hash  text NOT NULL UNIQUE,
    label       text,
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS share_links_active_idx ON share_links (tracker_id, expires_at) WHERE revoked_at IS NULL;

-- ----------------------------------------------------------------------------- RLS
-- Supabase: zapnout RLS bez politik = anon/authenticated klienti nevidí nic.
-- Ingestor se připojuje jako vlastník tabulek (postgres) a RLS obchází.
-- TODO: politiky pro appku (vlastník čte své trackery; share_links přes security definer funkci).
ALTER TABLE trackers     ENABLE ROW LEVEL SECURITY;
ALTER TABLE pets         ENABLE ROW LEVEL SECURITY;
ALTER TABLE events       ENABLE ROW LEVEL SECURITY;
ALTER TABLE positions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE hw_reports   ENABLE ROW LEVEL SECURITY;
ALTER TABLE health_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE zones        ENABLE ROW LEVEL SECURITY;
ALTER TABLE zone_events  ENABLE ROW LEVEL SECURITY;
ALTER TABLE geofence_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE geofence_state  ENABLE ROW LEVEL SECURITY;
ALTER TABLE commands     ENABLE ROW LEVEL SECURITY;
ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE notify_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE webauthn_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE share_links  ENABLE ROW LEVEL SECURITY;
