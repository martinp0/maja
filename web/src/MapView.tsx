import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { Position } from "./api";
import { fmtTime, sensorLabel } from "./format";
import { IconFit, IconLocate, IconMinus, IconPlus } from "./icons";
import { DESKTOP_MQ, isDesktop } from "./layout";

interface Props {
  latest: Position | null | undefined;
  track: Position[];
  petName: string;
  home: boolean | null | undefined;
  escaped?: boolean;
  live?: boolean;
  loading?: boolean;
  /** Domácí zóna z API (/api/geofence/state) – souřadnice se do PWA nikdy nehardcodují. */
  homeZone?: { lat: number; lng: number; radius_m: number } | null;
}

export interface MapHandle {
  recenter: () => void;
  fitTrack: () => void;
}

function prefersDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

/** Standardní dlaždice OSM. Bez API klíče; použití je osobní a řídké (viz tile usage policy).
 *  Přesně tato URL – subdomény a @2x OSM nepodporuje. Tmavý vzhled je filtr nad stejnými dlaždicemi. */
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

function readInset(): number {
  if (isDesktop()) return 0;
  const raw = getComputedStyle(document.querySelector(".shell") ?? document.documentElement).getPropertyValue(
    "--sheet-visible",
  );
  const n = parseFloat(raw);
  if (!Number.isFinite(n) || n <= 0) return Math.round(window.innerHeight * 0.46);
  return n;
}

/** Leaflet napřímo. Marker je vlastní tečka (Find My), dlaždice se přepínají se systémem. */
export const MapView = forwardRef<MapHandle, Props>(function MapView(
  { latest, track, petName, home, escaped, live, loading, homeZone },
  ref,
) {
  const elRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layersRef = useRef<{ marker: L.Marker; acc: L.Circle; line: L.Polyline } | null>(null);
  const centeredRef = useRef(false);
  const followRef = useRef(true);
  const zoneRef = useRef<L.Circle | null>(null);
  const latestRef = useRef(latest);
  const trackRef = useRef(track);
  latestRef.current = latest;
  trackRef.current = track;

  const focus = (ll: L.LatLngTuple, zoom: number, animate: boolean) => {
    const map = mapRef.current;
    if (!map) return;
    const pt = map.project(ll, zoom).add([0, readInset() / 2]);
    map.setView(map.unproject(pt, zoom), zoom, { animate });
  };

  useEffect(() => {
    if (!elRef.current || mapRef.current) return;
    const dark = prefersDark();
    elRef.current.classList.toggle("dark-map", dark);
    const map = L.map(elRef.current, {
      zoomControl: false,
      attributionControl: true,
      scrollWheelZoom: true,
    }).setView([49.8, 15.5], 7);
    map.attributionControl.setPosition("bottomleft");
    map.attributionControl.setPrefix(false);
    L.tileLayer(TILE_URL, {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    const line = L.polyline([], { color: dark ? "#64d2ff" : "#007aff", weight: 4, opacity: 0.9 }).addTo(map);
    const acc = L.circle([0, 0], {
      radius: 0,
      color: dark ? "#64d2ff" : "#007aff",
      weight: 1,
      fillColor: dark ? "#64d2ff" : "#007aff",
      fillOpacity: 0.08,
      interactive: false,
    }).addTo(map);
    const marker = L.marker([0, 0], {
      icon: pinIcon(false, false, false),
      opacity: 0,
      interactive: true,
      keyboard: false,
    }).addTo(map);
    layersRef.current = { marker, acc, line };
    mapRef.current = map;
    map.on("dragstart", () => {
      followRef.current = false;
    });
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onScheme = () => {
      const isDark = media.matches;
      elRef.current?.classList.toggle("dark-map", isDark);
      line.setStyle({ color: isDark ? "#64d2ff" : "#007aff" });
      const c = isDark ? "#64d2ff" : "#007aff";
      acc.setStyle({ color: c, fillColor: c });
    };
    media.addEventListener("change", onScheme);
    const onResize = () => map.invalidateSize();
    window.addEventListener("resize", onResize);
    const layoutMq = window.matchMedia(DESKTOP_MQ);
    const onLayout = () => {
      map.invalidateSize();
      const pos = latestRef.current;
      if (followRef.current && centeredRef.current && pos) {
        focus([pos.lat, pos.lng], map.getZoom(), false);
      }
    };
    layoutMq.addEventListener("change", onLayout);
    return () => {
      media.removeEventListener("change", onScheme);
      window.removeEventListener("resize", onResize);
      layoutMq.removeEventListener("change", onLayout);
      map.remove();
      mapRef.current = null;
      layersRef.current = null;
      zoneRef.current = null;
      centeredRef.current = false;
    };
  }, []);

  useEffect(() => {
    const layers = layersRef.current;
    const map = mapRef.current;
    if (!layers || !map) return;
    layers.line.setLatLngs(track.map((p) => [p.lat, p.lng] as L.LatLngTuple));
    if (!latest) {
      layers.marker.setOpacity(0);
      return;
    }
    const ll: L.LatLngTuple = [latest.lat, latest.lng];
    layers.marker.setLatLng(ll);
    layers.marker.setOpacity(1);
    layers.marker.setIcon(pinIcon(!!home, !!escaped, !!live));
    layers.marker.bindTooltip(`${petName} · ${fmtTime(latest.ts)} · ${sensorLabel(latest.sensor_used)}`, {
      direction: "top",
      offset: [0, -14],
    });
    layers.acc.setLatLng(ll);
    layers.acc.setRadius(latest.accuracy_m ?? 0);
    if (!centeredRef.current) {
      focus(ll, 16, false);
      centeredRef.current = true;
      followRef.current = true;
    } else if (followRef.current) {
      focus(ll, map.getZoom(), false);
    }
  }, [latest, track, petName, home, escaped, live]);

  const zLat = homeZone?.lat;
  const zLng = homeZone?.lng;
  const zR = homeZone?.radius_m;
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (zLat == null || zLng == null || zR == null) {
      zoneRef.current?.remove();
      zoneRef.current = null;
      return;
    }
    if (!zoneRef.current) {
      zoneRef.current = L.circle([zLat, zLng], {
        radius: zR,
        color: "#34c759",
        weight: 1.5,
        dashArray: "5 7",
        fillColor: "#34c759",
        fillOpacity: 0.07,
        interactive: false,
      }).addTo(map);
      zoneRef.current.bringToBack();
    } else {
      zoneRef.current.setLatLng([zLat, zLng]);
      zoneRef.current.setRadius(zR);
    }
  }, [zLat, zLng, zR]);

  useImperativeHandle(ref, () => ({
    recenter: () => {
      const pos = latestRef.current;
      const map = mapRef.current;
      if (!pos || !map) return;
      followRef.current = true;
      focus([pos.lat, pos.lng], Math.max(map.getZoom(), 16), true);
    },
    fitTrack: () => {
      const map = mapRef.current;
      const pts = trackRef.current;
      if (!map || pts.length < 2) return;
      followRef.current = false;
      const inset = readInset();
      map.fitBounds(L.latLngBounds(pts.map((p) => [p.lat, p.lng] as L.LatLngTuple)), {
        paddingTopLeft: [28, 88],
        paddingBottomRight: [28, inset + 24],
        animate: true,
      });
    },
  }));

  const hasFix = !!latest;

  return (
    <div className="map-wrap">
      <div ref={elRef} className="map" />
      <div className="map-scrim" aria-hidden />
      {live && (
        <div className="live-pill">
          <span className="live-dot" />
          Živě
        </div>
      )}
      <div className="map-buttons">
        <button
          type="button"
          className="map-btn desktop-only"
          onClick={() => mapRef.current?.zoomIn()}
          aria-label="Přiblížit"
        >
          <IconPlus />
        </button>
        <button
          type="button"
          className="map-btn desktop-only"
          onClick={() => mapRef.current?.zoomOut()}
          aria-label="Oddálit"
        >
          <IconMinus />
        </button>
        <button
          type="button"
          className="map-btn"
          onClick={() => {
            const pos = latestRef.current;
            const map = mapRef.current;
            if (!pos || !map) return;
            followRef.current = true;
            focus([pos.lat, pos.lng], Math.max(map.getZoom(), 16), true);
          }}
          disabled={!hasFix}
          aria-label={`Vycentrovat na ${petName}`}
        >
          <IconLocate />
        </button>
        <button
          type="button"
          className="map-btn"
          onClick={() => {
            const map = mapRef.current;
            const pts = trackRef.current;
            if (!map || pts.length < 2) return;
            followRef.current = false;
            map.fitBounds(L.latLngBounds(pts.map((p) => [p.lat, p.lng] as L.LatLngTuple)), {
              paddingTopLeft: [28, 88],
              paddingBottomRight: [28, readInset() + 24],
              animate: true,
            });
          }}
          disabled={track.length < 2}
          aria-label="Zobrazit celou trasu"
        >
          <IconFit />
        </button>
      </div>
      {!hasFix && (
        <div className="map-empty">
          <span>{loading ? "Načítám polohu" : "Zatím žádná poloha"}</span>
        </div>
      )}
    </div>
  );
});

function pinIcon(home: boolean, escaped: boolean, live: boolean): L.DivIcon {
  const kind = escaped ? "escaped" : home ? "home" : "away";
  return L.divIcon({
    className: "dog-pin",
    html: `<div class="dog-marker ${kind}${live ? " live" : ""}"><span class="pulse"></span><span class="dot"></span></div>`,
    iconSize: [36, 36],
    iconAnchor: [18, 18],
  });
}
