import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { usePolling } from "./usePolling";
import { MapView, type MapHandle } from "./MapView";
import { LiveAction, StatusGlance } from "./StatusCard";
import { ActivityCard } from "./ActivityCard";
import { CommandPanel } from "./CommandPanel";
import { HomeBadge } from "./HomeBadge";
import { GeofenceHistory } from "./GeofenceHistory";
import { PasskeyOffer } from "./SecurityCard";
import { BottomSheet, type Snap } from "./BottomSheet";
import { SettingsView } from "./SettingsView";
import { tick } from "./haptic";
import { IconSettings } from "./icons";

const OPEN = new Set(["queued", "sending", "sent", "deferred"]);

function storedSnap(): Snap {
  try {
    const v = sessionStorage.getItem("maja-snap");
    if (v === "peek" || v === "mid" || v === "full") return v;
  } catch {
    /* soukromý režim */
  }
  return "mid";
}

function storedSettings(): boolean {
  try {
    return sessionStorage.getItem("maja-settings") === "1";
  } catch {
    return false;
  }
}

export default function App({ onLogout, offerPasskey = false }: { onLogout?: () => void; offerPasskey?: boolean }) {
  const trackers = usePolling(api.trackers, 5 * 60_000);
  const pets = usePolling(api.pets, 30 * 60_000);

  // ?tracker=XYZ přepíše výběr; jinak první tracker (Mája)
  const wanted = new URLSearchParams(window.location.search).get("tracker");
  const tracker = trackers.data?.find((t) => t.tracker_id === wanted) ?? trackers.data?.[0];
  const trackerId = tracker?.tracker_id;
  const pet = pets.data?.find((p) => p.tracker_id === trackerId);
  const petName = pet?.name ?? tracker?.pet_name ?? "Mája";

  const latestFn = useCallback(() => api.latest(trackerId!), [trackerId]);
  const trackFn = useCallback(() => api.positions(trackerId!), [trackerId]);
  const healthFn = useCallback(() => api.health(trackerId!), [trackerId]);
  const commandsFn = useCallback(() => api.commands(trackerId!, 8), [trackerId]);
  const geoFn = useCallback(() => api.geofenceState(trackerId!), [trackerId]);
  const geoEventsFn = useCallback(() => api.geofenceEvents(trackerId!, 20), [trackerId]);

  // Při běžící živé poloze (LIVE) obnovovat polohu i trasu po 5 s, jinak 20 s / 60 s
  const [liveFast, setLiveFast] = useState(false);
  const latest = usePolling(trackerId ? latestFn : null, liveFast ? 5_000 : 20_000, [trackerId]);
  const track = usePolling(trackerId ? trackFn : null, liveFast ? 10_000 : 60_000, [trackerId]);
  useEffect(() => {
    const sw = latest.data?.tracker?.last_switches?.live_tracking;
    const on = !!sw?.active && (!sw.at || sw.remaining == null || (sw.at + sw.remaining) * 1000 > Date.now());
    setLiveFast(on);
  }, [latest.data]);
  const health = usePolling(trackerId ? healthFn : null, 5 * 60_000, [trackerId]);
  const geo = usePolling(trackerId ? geoFn : null, 20_000, [trackerId]);
  const geoEvents = usePolling(trackerId ? geoEventsFn : null, 2 * 60_000, [trackerId]);
  // změna stavu (odchod/návrat) -> hned obnovit historii
  const geoState = geo.data?.state;
  const refreshGeoEvents = geoEvents.refresh;
  useEffect(() => {
    if (geoState) refreshGeoEvents();
  }, [geoState, refreshGeoEvents]);
  // Když něco čeká, zrychlit polling povelů (3 s), ať je vidět queued → deferred → sent → done
  const [anyOpen, setAnyOpen] = useState(false);
  const cmds = usePolling(trackerId ? commandsFn : null, anyOpen ? 3_000 : 20_000, [trackerId]);
  useEffect(() => {
    setAnyOpen(cmds.data?.some((c) => OPEN.has(c.status)) ?? false);
  }, [cmds.data]);

  const onSent = () => {
    setAnyOpen(true);
    cmds.refresh();
  };

  const err = trackers.error ?? latest.error;
  const positions = useMemo(() => track.data?.positions ?? [], [track.data]);
  const g = geo.data;
  const homeZone = useMemo(
    () => (g?.enabled && g.home ? { lat: g.home.lat, lng: g.home.lng, radius_m: g.home_radius_m } : null),
    [g?.enabled, g?.home?.lat, g?.home?.lng, g?.home_radius_m],
  );
  // barva markeru: debouncovaný stav geofence, když je známý; jinak příznak z Tractive
  const isHome = g?.enabled && g.state !== "unknown" ? g.state === "home" : latest.data?.in_home_zone;

  const mapRef = useRef<MapHandle>(null);
  // Složení/rozevření někdy stránku znovu načte. Poslední list a nastavení zůstanou v session.
  const [snap, setSnap] = useState<Snap>(storedSnap);
  const [settings, setSettings] = useState(storedSettings);
  useEffect(() => {
    try {
      sessionStorage.setItem("maja-snap", snap);
    } catch {
      /* ignore */
    }
  }, [snap]);
  useEffect(() => {
    try {
      sessionStorage.setItem("maja-settings", settings ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [settings]);
  const [offline, setOffline] = useState(() => (typeof navigator !== "undefined" ? !navigator.onLine : false));
  useEffect(() => {
    const on = () => setOffline(false);
    const off = () => setOffline(true);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  const booting = !trackers.data && !trackers.error;
  const noTracker = !!trackers.data && trackers.data.length === 0;

  const showTrack = () => {
    tick(8);
    mapRef.current?.fitTrack();
    setSnap("peek");
  };

  return (
    <div className="shell">
      <MapView
        ref={mapRef}
        latest={latest.data?.position}
        track={positions}
        petName={petName}
        home={isHome}
        escaped={!!g?.escaped}
        live={liveFast}
        loading={booting || (!!trackerId && latest.loading && !latest.data)}
        homeZone={homeZone}
      />

      <BottomSheet
        snap={snap}
        onSnap={setSnap}
        head={
          <>
            <div className="sheet-title">
              <div className="name-block">
                <h1>{petName}</h1>
                <HomeBadge geo={g} />
              </div>
              <button
                type="button"
                className="icon-btn"
                aria-label="Nastavení"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => {
                  tick(8);
                  setSettings(true);
                }}
              >
                <IconSettings />
              </button>
            </div>
            {booting ? <GlanceSkeleton /> : <StatusGlance latest={latest.data} />}
          </>
        }
      >
        <PasskeyOffer show={offerPasskey} />
        {offline && (
          <div className="alert neutral" role="status">
            Jsi offline. Poloha se obnoví, až se vrátí spojení.
          </div>
        )}
        {err && (
          <div className="alert" role="alert">
            {err instanceof ApiError && err.status === 503 ? "Databáze je teď nedostupná." : `Chyba: ${err.message}`}
          </div>
        )}
        {noTracker && (
          <div className="empty-block">
            <h2>Žádný obojek</h2>
            <p>V databázi zatím není tracker. Zkontroluj, jestli běží ingestor.</p>
          </div>
        )}
        {!booting && !noTracker && (
          <>
            <LiveAction
              latest={latest.data}
              petName={petName}
              trackerId={trackerId}
              commands={cmds.data}
              onSent={onSent}
            />
            {trackerId && (
              <CommandPanel
                trackerId={trackerId}
                petName={petName}
                home={latest.data?.in_home_zone}
                commands={cmds.data}
                onSent={onSent}
              />
            )}
            <GeofenceHistory events={geoEvents.data} geo={g} />
            <section className="block">
              <div className="block-head">
                <h2>Trasa</h2>
                <button type="button" className="text-btn" onClick={showTrack} disabled={positions.length < 2}>
                  Zobrazit
                </button>
              </div>
              <p className="empty">
                {positions.length
                  ? `${positions.length} ${positions.length === 1 ? "bod" : "bodů"} za posledních 24 hodin`
                  : "Za posledních 24 hodin zatím žádný bod."}
              </p>
            </section>
            <ActivityCard health={health.data} />
            <p className="footnote">Časy jsou v Europe/Prague. Trasa je z posledních 24 hodin.</p>
          </>
        )}
      </BottomSheet>

      {settings && (
        <SettingsView
          petName={petName}
          model={tracker?.model_number}
          geo={g}
          onClose={() => setSettings(false)}
          onLogout={onLogout}
        />
      )}
    </div>
  );
}

function GlanceSkeleton() {
  return (
    <div className="glance skeleton" aria-hidden>
      <span className="sk sk-time" />
      <span className="sk sk-line" />
      <span className="sk-row">
        <span className="sk" />
        <span className="sk" />
        <span className="sk" />
      </span>
    </div>
  );
}
