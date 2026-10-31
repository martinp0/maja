// Obývací TV: jen čtení (stejný read-only token jako iPhone). Povely na dálkovém ovladači
// nechceme — bzučák omylem z gauče. Tizen 6.5 by React 19 z telefonní PWA neutáhl.
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { GeofenceState, Latest, Pet, Position, TrackerSummary } from "../api";
import { fmtAgo, fmtClock, fmtDistance, sensorLabel } from "../format";
import "./tv.css";

const ORIGIN_KEY = "maja.tv.origin";
const TOKEN_KEY = "maja.tv.token";

const nameEl = document.querySelector<HTMLElement>("#name")!;
const placeEl = document.querySelector<HTMLElement>("#place")!;
const batteryEl = document.querySelector<HTMLElement>("#battery")!;
const seenEl = document.querySelector<HTMLElement>("#seen")!;
const sensorEl = document.querySelector<HTMLElement>("#sensor")!;
const linkEl = document.querySelector<HTMLElement>("#link")!;
const bannerEl = document.querySelector<HTMLElement>("#banner")!;
const fitBtn = document.querySelector<HTMLButtonElement>("#fit")!;
const refreshBtn = document.querySelector<HTMLButtonElement>("#refresh")!;
const serverBtn = document.querySelector<HTMLButtonElement>("#server")!;
const setupEl = document.querySelector<HTMLElement>("#setup")!;
const form = document.querySelector<HTMLFormElement>("#setup-form")!;
const originInput = document.querySelector<HTMLInputElement>("#origin")!;
const tokenInput = document.querySelector<HTMLInputElement>("#token")!;
const setupError = document.querySelector<HTMLElement>("#setup-error")!;
const mapEl = document.querySelector<HTMLElement>("#map")!;

let origin = localStorage.getItem(ORIGIN_KEY) ?? "";
let token = localStorage.getItem(TOKEN_KEY) ?? "";
let timer = 0;
let latestFix: Position | null = null;
let track: Position[] = [];

const map = L.map(mapEl, { zoomControl: false, attributionControl: true, keyboard: false }).setView([49.8, 15.5], 7);
map.attributionControl.setPrefix(false);
map.attributionControl.setPosition("bottomleft");
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
const line = L.polyline([], { color: "#64d2ff", weight: 6, opacity: 0.9 }).addTo(map);
const marker = L.circleMarker([0, 0], {
  radius: 16,
  color: "#0a84ff",
  weight: 4,
  fillColor: "#ffffff",
  fillOpacity: 1,
}).addTo(map);
marker.setStyle({ opacity: 0, fillOpacity: 0 });
let zone: L.Circle | null = null;

function normalizeOrigin(input: string): string | null {
  let url: URL;
  try {
    url = new URL(input.trim());
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (url.username || url.password) return null;
  if (url.pathname !== "/" && url.pathname !== "") return null;
  if (url.search || url.hash) return null;
  return url.origin;
}

function showBanner(text: string | null) {
  if (!text) {
    bannerEl.hidden = true;
    bannerEl.textContent = "";
    return;
  }
  bannerEl.hidden = false;
  bannerEl.textContent = text;
}

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${origin}/api${path}`, {
    cache: "no-store",
    headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) throw new Error("Token nefunguje. Zkontroluj API_READ_TOKENS.");
  if (res.status === 403) throw new Error("Token je jen na čtení a na tuhle adresu nestačí.");
  if (!res.ok) throw new Error(`Server odpověděl ${res.status}.`);
  return res.json() as Promise<T>;
}

function paint(latest: Latest | null, geo: GeofenceState | null, petName: string) {
  nameEl.textContent = petName;
  const pos = latest?.position ?? null;
  latestFix = pos;
  if (geo?.enabled && geo.state === "home") {
    placeEl.textContent = "Doma";
    placeEl.className = "place home";
  } else if (geo?.enabled && geo.escaped) {
    placeEl.textContent = "Utekla";
    placeEl.className = "place escaped";
  } else if (geo?.enabled && geo.state === "away") {
    const dist = geo.distance_m != null ? ` · ${fmtDistance(geo.distance_m)}` : "";
    placeEl.textContent = `Venku${dist}`;
    placeEl.className = "place away";
  } else {
    placeEl.textContent = pos ? "Poloha je, domov není nastavený" : "Zatím žádná poloha";
    placeEl.className = "place unknown";
  }
  const battery = latest?.hw?.battery_pct;
  batteryEl.textContent = battery == null ? "–" : `${battery} %`;
  seenEl.textContent = pos ? fmtClock(pos.ts) : "–";
  sensorEl.textContent = pos ? `${fmtAgo(pos.ts)} · ${sensorLabel(pos.sensor_used)}` : "";
  const contact = latest?.tracker?.updated_at ?? latest?.hw?.ts ?? null;
  linkEl.textContent = contact ? fmtAgo(contact) : "–";

  line.setLatLngs(track.map((p) => [p.lat, p.lng]));
  if (pos) {
    marker.setLatLng([pos.lat, pos.lng]);
    marker.setStyle({ opacity: 1, fillOpacity: 1 });
    if (!map.getBounds().contains([pos.lat, pos.lng]) || map.getZoom() < 8) {
      map.setView([pos.lat, pos.lng], Math.max(map.getZoom(), 16), { animate: false });
    }
  }
  fitBtn.disabled = track.length < 2;
  if (geo?.enabled && geo.home) {
    const ll: L.LatLngExpression = [geo.home.lat, geo.home.lng];
    if (!zone) {
      zone = L.circle(ll, {
        radius: geo.home_radius_m,
        color: "#30d158",
        weight: 2,
        dashArray: "8 10",
        fillColor: "#30d158",
        fillOpacity: 0.08,
      }).addTo(map);
    } else {
      zone.setLatLng(ll);
      zone.setRadius(geo.home_radius_m);
    }
  } else if (zone) {
    zone.remove();
    zone = null;
  }
}

async function refresh() {
  showBanner(null);
  const trackers = await apiGet<TrackerSummary[]>("/trackers");
  const tracker = trackers[0];
  if (!tracker) {
    nameEl.textContent = "Mája";
    placeEl.textContent = "Žádný obojek";
    placeEl.className = "place unknown";
    return;
  }
  const id = encodeURIComponent(tracker.tracker_id);
  const [pets, latest, positions, geo] = await Promise.all([
    apiGet<Pet[]>("/pets"),
    apiGet<Latest>(`/trackers/${id}/latest`),
    apiGet<{ positions: Position[] }>(`/trackers/${id}/positions`),
    apiGet<GeofenceState>("/geofence/state"),
  ]);
  track = positions.positions ?? [];
  const pet = pets.find((p) => p.tracker_id === tracker.tracker_id);
  paint(latest, geo, pet?.name || tracker.pet_name || "Mája");
  map.invalidateSize();
}

function arm() {
  window.clearInterval(timer);
  timer = window.setInterval(() => {
    refresh().catch((err: unknown) => showBanner(err instanceof Error ? err.message : "Chyba spojení"));
  }, 20_000);
}

function openSetup() {
  originInput.value = origin;
  tokenInput.value = token;
  setupError.hidden = true;
  setupEl.hidden = false;
  originInput.focus();
}

function closeSetup() {
  setupEl.hidden = true;
  mapEl.focus();
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const next = normalizeOrigin(originInput.value);
  const nextToken = tokenInput.value.trim();
  if (!next) {
    setupError.hidden = false;
    setupError.textContent = "Adresa musí být http nebo https, jen server, bez cesty.";
    return;
  }
  if (nextToken.length < 32) {
    setupError.hidden = false;
    setupError.textContent = "Token má aspoň 32 znaků.";
    return;
  }
  origin = next;
  token = nextToken;
  localStorage.setItem(ORIGIN_KEY, origin);
  localStorage.setItem(TOKEN_KEY, token);
  closeSetup();
  refresh()
    .then(arm)
    .catch((err: unknown) => showBanner(err instanceof Error ? err.message : "Chyba spojení"));
});

fitBtn.addEventListener("click", () => {
  if (track.length < 2) return;
  map.fitBounds(L.latLngBounds(track.map((p) => [p.lat, p.lng])), { padding: [48, 48], animate: true });
});
refreshBtn.addEventListener("click", () => {
  refresh().catch((err: unknown) => showBanner(err instanceof Error ? err.message : "Chyba spojení"));
});
serverBtn.addEventListener("click", openSetup);

function focusables(): HTMLElement[] {
  const root: ParentNode = setupEl.hidden ? document : setupEl;
  return [...root.querySelectorAll<HTMLElement>(".focusable")].filter(
    (el) => !el.hasAttribute("disabled") && !el.closest("[hidden]"),
  );
}

function moveFocus(delta: number) {
  const items = focusables();
  if (!items.length) return;
  const i = items.indexOf(document.activeElement as HTMLElement);
  const next = items[(i + delta + items.length) % items.length];
  next.focus();
}

document.addEventListener("keydown", (e) => {
  const code = e.keyCode;
  const back = code === 10009 || code === 461 || e.key === "Escape" || e.key === "Backspace" && document.activeElement?.tagName !== "INPUT";
  if (back && !setupEl.hidden && document.activeElement?.tagName !== "INPUT") {
    if (origin && token) {
      e.preventDefault();
      closeSetup();
    }
    return;
  }
  if (document.activeElement === mapEl && !e.altKey && !e.metaKey) {
    const step = 160;
    if (e.key === "ArrowLeft") {
      map.panBy([-step, 0], { animate: true });
      e.preventDefault();
      return;
    }
    if (e.key === "ArrowRight") {
      map.panBy([step, 0], { animate: true });
      e.preventDefault();
      return;
    }
    if (e.key === "ArrowUp") {
      map.panBy([0, -step], { animate: true });
      e.preventDefault();
      return;
    }
    if (e.key === "ArrowDown") {
      map.panBy([0, step], { animate: true });
      e.preventDefault();
      return;
    }
    if (e.key === "Enter") {
      if (latestFix) map.setView([latestFix.lat, latestFix.lng], 16, { animate: true });
      e.preventDefault();
      return;
    }
    if (code === 427 || e.key === "+" || e.key === "=") {
      map.zoomIn();
      e.preventDefault();
      return;
    }
    if (code === 428 || e.key === "-" || e.key === "_") {
      map.zoomOut();
      e.preventDefault();
      return;
    }
  }
  if (document.activeElement?.tagName === "INPUT" && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
  if (e.key === "ArrowDown" || e.key === "ArrowRight") {
    moveFocus(1);
    e.preventDefault();
  } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
    moveFocus(-1);
    e.preventDefault();
  }
});

window.addEventListener("resize", () => map.invalidateSize());

if (!origin || !token) {
  openSetup();
} else {
  mapEl.focus();
  refresh()
    .then(arm)
    .catch((err: unknown) => showBanner(err instanceof Error ? err.message : "Chyba spojení"));
}
