// Snímky skládacích rozložení a televize. Data jsou ukázková (Luna, Central Park), ne domov.
import { spawn } from "node:child_process";
import { mkdir, copyFile } from "node:fs/promises";
import { chromium } from "playwright-core";

const OUT = new URL("../../docs/screenshots/samsung/", import.meta.url);
const ART = "/opt/cursor/artifacts/screenshots";
const NOW = new Date().toISOString();

const trackers = [
  {
    tracker_id: "DEMO-LUNA",
    model_number: "TRNJA4",
    state: "OPERATIONAL",
    state_reason: null,
    pet_id: "pet-luna",
    pet_name: "Luna",
    battery_pct: 76,
    power_saving: false,
    last_position_ts: NOW,
    sensor_used: "GPS",
  },
];
const pets = [{ pet_id: "pet-luna", tracker_id: "DEMO-LUNA", name: "Luna", daily_active_minutes_goal: 60 }];
const positions = [
  [40.7824, -73.9674],
  [40.7819, -73.9662],
  [40.7812, -73.9656],
  [40.7806, -73.9664],
  [40.781, -73.9672],
  [40.7816, -73.9668],
].map(([lat, lng], i) => ({
  ts: new Date(Date.now() - (6 - i) * 120_000).toISOString(),
  lat,
  lng,
  accuracy_m: 8,
  sensor_used: "GPS",
}));
const latest = {
  tracker_id: "DEMO-LUNA",
  position: { ...positions.at(-1), power_saving_zone_id: null },
  hw: {
    ts: NOW,
    battery_pct: 76,
    charging: false,
    charging_state: "NOT_CHARGING",
    tracker_state: "OPERATIONAL",
    power_saving: false,
  },
  tracker: { state: "OPERATIONAL", state_reason: null, charging_state: "NOT_CHARGING", updated_at: NOW, last_switches: {} },
  in_home_zone: true,
};
const geo = {
  enabled: true,
  tracker_id: "DEMO-LUNA",
  state: "home",
  since: new Date(Date.now() - 20 * 60_000).toISOString(),
  distance_m: 18,
  in_home_zone: true,
  home_radius_m: 80,
  home: { lat: 40.7812, lng: -73.9665 },
  escape_m: 300,
  escaped: false,
  pending_state: null,
  pending_since: null,
  last_fix_ts: NOW,
  schema_ready: true,
};

function json(body) {
  return { status: 200, contentType: "application/json", body: JSON.stringify(body) };
}

function apiBody(pathname) {
  if (pathname.endsWith("/api/me")) return json({ authed: true, auth_enabled: false, passkey_count: 0 });
  if (pathname.endsWith("/api/trackers")) return json(trackers);
  if (pathname.endsWith("/api/pets")) return json(pets);
  if (pathname.endsWith("/latest")) return json(latest);
  if (pathname.endsWith("/positions")) return json({ positions });
  if (pathname.endsWith("/health")) return json({ tracker_id: "DEMO-LUNA", pet_id: "pet-luna", latest: null, days: [] });
  if (pathname.endsWith("/commands")) return json([]);
  if (pathname.endsWith("/geofence/state")) return json(geo);
  if (pathname.endsWith("/geofence/events")) return json([]);
  if (pathname.endsWith("/push/key")) return json({ enabled: false, public_key: null });
  return null;
}

function start(cmd, args, cwd) {
  const child = spawn(cmd, args, { cwd, stdio: "inherit" });
  return child;
}

async function waitFor(url) {
  for (let i = 0; i < 80; i++) {
    try {
      const res = await fetch(url);
      if (res.ok || res.status === 404) return;
    } catch {
      /* not up */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`server did not start: ${url}`);
}

const preview = start("npx", ["vite", "preview", "--host", "127.0.0.1", "--port", "4173", "--strictPort"], new URL("..", import.meta.url).pathname);
const tv = start("python3", ["-m", "http.server", "4174", "--bind", "127.0.0.1"], new URL("../../tizen/package/", import.meta.url).pathname);

try {
  await waitFor("http://127.0.0.1:4173/");
  await waitFor("http://127.0.0.1:4174/index.html");
  await mkdir(OUT, { recursive: true });
  await mkdir(ART, { recursive: true });

  const browser = await chromium.launch({
    executablePath: process.env.CHROME || "/usr/local/bin/google-chrome",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });

  async function shotPhone(name, viewport, previewFold, scheme = "light") {
    const page = await browser.newPage({ viewport, deviceScaleFactor: name === "tv" ? 1 : 2 });
    await page.emulateMedia({ colorScheme: scheme });
    if (previewFold) await page.addInitScript((fold) => { window.__MAJA_FOLD_PREVIEW__ = fold; }, previewFold);
    await page.route("**/sw.js", (route) =>
      route.fulfill({ status: 200, contentType: "text/javascript", body: "self.addEventListener('install',()=>self.skipWaiting())" }),
    );
    await page.route("**/api/**", (route) => {
      const hit = apiBody(new URL(route.request().url()).pathname);
      if (!hit) return route.fulfill({ status: 404, body: "{}" });
      return route.fulfill(hit);
    });
    await page.goto("http://127.0.0.1:4173/", { waitUntil: "networkidle" });
    await page.waitForSelector(".place, .sheet-title h1", { timeout: 20000 });
    await page.waitForTimeout(1600);
    const file = new URL(name, OUT);
    await page.screenshot({ path: file.pathname });
    await page.close();
  }

  await shotPhone("compact.png", { width: 390, height: 844 }, null);
  await shotPhone("flex.png", { width: 412, height: 880 }, { fold: "flex", hingeTop: 400, hingeHeight: 28 });
  await shotPhone("expanded.png", { width: 960, height: 800 }, { fold: "book", hingeLeft: 420, hingeWidth: 28 });
  await shotPhone("cover.png", { width: 360, height: 392 }, { fold: "cover" });

  const tvPage = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await tvPage.addInitScript(() => {
    localStorage.setItem("maja.tv.origin", "http://127.0.0.1:4174");
    localStorage.setItem("maja.tv.token", "a".repeat(32));
  });
  await tvPage.route("**/api/**", (route) => {
    const hit = apiBody(new URL(route.request().url()).pathname);
    if (!hit) return route.fulfill({ status: 404, body: "{}" });
    return route.fulfill(hit);
  });
  await tvPage.goto("http://127.0.0.1:4174/index.html", { waitUntil: "networkidle" });
  await tvPage.waitForSelector("#place", { timeout: 20000 });
  await tvPage.waitForTimeout(1600);
  const tvFile = new URL("tv.png", OUT);
  await tvPage.screenshot({ path: tvFile.pathname });
  await tvPage.close();
  await browser.close();

  for (const name of ["compact.png", "flex.png", "expanded.png", "cover.png", "tv.png"]) {
    await copyFile(new URL(name, OUT), `${ART}/${name}`);
  }
  console.log("screenshots ok");
} finally {
  preview.kill("SIGTERM");
  tv.kill("SIGTERM");
}
