// Tenký klient nad tractive-api. Base URL z VITE_API_BASE (build-time), default same-origin "/api".
const BASE = ((import.meta.env.VITE_API_BASE as string | undefined) || "/api").replace(/\/$/, "");

export type Iso = string;

export interface Pet {
  pet_id: string;
  tracker_id: string | null;
  name: string | null;
  daily_active_minutes_goal: number | null;
}

export interface TrackerSummary {
  tracker_id: string;
  model_number: string | null;
  state: string | null;
  state_reason: string | null;
  pet_id: string | null;
  pet_name: string | null;
  battery_pct: number | null;
  power_saving: boolean | null;
  last_position_ts: Iso | null;
  sensor_used: string | null;
}

export interface Position {
  ts: Iso;
  lat: number;
  lng: number;
  accuracy_m: number | null;
  sensor_used: string | null;
}

export interface Latest {
  tracker_id: string;
  position: (Position & { power_saving_zone_id: string | null }) | null;
  hw: {
    ts: Iso;
    battery_pct: number | null;
    charging: boolean | null;
    charging_state: string | null;
    tracker_state: string | null;
    power_saving: boolean | null;
  } | null;
  tracker: {
    state: string | null;
    state_reason: string | null;
    charging_state: string | null;
    power_saving_zone_id?: string | null;
    updated_at?: Iso | null;
    last_switches?: Record<string, { active?: boolean | null; remaining?: number | null; at?: number | null }> | null;
  } | null;
  in_home_zone: boolean | null;
}

export interface HealthDay {
  day: string;
  synced_at: Iso | null;
  minutes_active: number | null;
  minutes_goal: number | null;
  sleep_day_min: number | null;
  sleep_night_min: number | null;
  sleep_calm_min: number | null;
}

export interface Health {
  tracker_id: string;
  pet_id: string | null;
  pet_name?: string | null;
  daily_active_minutes_goal?: number | null;
  latest: HealthDay | null;
  days: HealthDay[];
}

export type CommandStatus = "queued" | "sending" | "sent" | "done" | "deferred" | "failed";

export interface CommandRow {
  id: string;
  tracker_id: string;
  command: string; // live | buzzer | led
  action: "on" | "off";
  status: CommandStatus;
  attempts: number;
  last_error: string | null;
  created_at: Iso;
  updated_at: Iso;
  next_attempt_at: Iso | null;
  finished_at: Iso | null;
  in_home_zone?: boolean | null;
  note?: string | null;
}

export type GeofenceStateName = "home" | "away" | "unknown";

export interface GeofenceState {
  enabled: boolean;
  tracker_id: string | null;
  state: GeofenceStateName;
  since: Iso | null;
  distance_m: number | null;
  in_home_zone: boolean | null;
  home_radius_m: number;
  home: { lat: number; lng: number } | null;
  escape_m: number;
  escaped: boolean;
  pending_state: "home" | "away" | null;
  pending_since: Iso | null;
  last_fix_ts: Iso | null;
  schema_ready: boolean;
}

export interface GeofenceEvent {
  id: number;
  tracker_id: string;
  event_type: "left_home" | "returned_home";
  happened_at: Iso;
  distance_m: number | null;
  created_at: Iso;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Vyvolá se, když data API vrátí 401 (session vypršela / odhlášení) -> appka ukáže přihlášení. */
export const UNAUTHORIZED_EVENT = "maja:unauthorized";

function doFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(BASE + path, {
    // nikdy z HTTP cache prohlížeče – data musí být čerstvá
    cache: "no-store",
    ...init,
    credentials: "include",
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
  });
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await doFetch(path, init);
  if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export type LoginResult = "ok" | "bad_pin" | "rate_limited" | "error";

export interface Me {
  authed: boolean;
  auth_enabled?: boolean;
  passkey_count?: number | null;
}

export interface Passkey {
  id: number;
  credential_id: string;
  name: string | null;
  backed_up: boolean | null;
  created_at: Iso;
  last_used_at: Iso | null;
}

export const auth = {
  me: async (): Promise<Me> => {
    const res = await doFetch("/me", { cache: "no-store" });
    if (!res.ok) throw new ApiError(res.status, res.statusText || `HTTP ${res.status}`);
    return res.json();
  },
  login: async (password: string): Promise<LoginResult> => {
    try {
      // Pole `pin` kvůli kompatibilitě se starším API (nové bere i `password`).
      const res = await doFetch("/login", { method: "POST", body: JSON.stringify({ pin: password }) });
      if (res.ok) return "ok";
      if (res.status === 401) return "bad_pin";
      if (res.status === 429) return "rate_limited";
      return "error";
    } catch {
      return "error";
    }
  },
  logout: async (): Promise<void> => {
    try {
      await doFetch("/logout", { method: "POST" });
    } finally {
      // offline cache service workeru s daty z API po odhlášení zahodit
      if ("caches" in window) await caches.delete("api").catch(() => false);
    }
  },
};

/** Passkeys (WebAuthn). Options JSON jde rovnou do @simplewebauthn/browser. */
export const webauthn = {
  registerOptions: () => req<Record<string, unknown>>("/webauthn/register/options", { method: "POST" }),
  registerVerify: (credential: unknown) =>
    req<{ ok: boolean; credential: Passkey }>("/webauthn/register/verify", {
      method: "POST",
      body: JSON.stringify({ credential }),
    }),
  loginOptions: async (): Promise<Record<string, unknown> | "none" | "rate_limited"> => {
    const res = await doFetch("/webauthn/login/options", { method: "POST" });
    if (res.status === 404) return "none";
    if (res.status === 429) return "rate_limited";
    if (!res.ok) throw new ApiError(res.status, res.statusText || `HTTP ${res.status}`);
    return res.json();
  },
  loginVerify: async (credential: unknown): Promise<LoginResult> => {
    const res = await doFetch("/webauthn/login/verify", { method: "POST", body: JSON.stringify(credential) });
    if (res.ok) return "ok";
    if (res.status === 429) return "rate_limited";
    if (res.status === 401) return "bad_pin";
    return "error";
  },
  list: () => req<Passkey[]>("/webauthn/credentials"),
  remove: (id: number) => req<{ ok: boolean }>(`/webauthn/credentials/${id}/delete`, { method: "POST" }),
};

const t = (id: string) => `/trackers/${encodeURIComponent(id)}`;

export const api = {
  pets: () => req<Pet[]>("/pets"),
  trackers: () => req<TrackerSummary[]>("/trackers"),
  latest: (id: string) => req<Latest>(`${t(id)}/latest`),
  positions: (id: string, sinceIso?: string, limit = 2000) => {
    const q = new URLSearchParams({ limit: String(limit) });
    if (sinceIso) q.set("since", sinceIso);
    return req<{ count: number; positions: Position[] }>(`${t(id)}/positions?${q}`);
  },
  health: (id: string) => req<Health>(`${t(id)}/health`),
  commands: (id: string, limit = 10) => req<CommandRow[]>(`${t(id)}/commands?limit=${limit}`),
  geofenceState: (id: string) => req<GeofenceState>(`/geofence/state?tracker_id=${encodeURIComponent(id)}`),
  geofenceEvents: (id: string, limit = 20) =>
    req<GeofenceEvent[]>(`/geofence/events?tracker_id=${encodeURIComponent(id)}&limit=${limit}`),
  pushKey: () => req<{ enabled: boolean; public_key: string | null }>("/push/vapid-public-key"),
  pushSubscribe: (sub: PushSubscriptionJSON) =>
    req<{ ok: boolean; id: number }>("/push/subscribe", { method: "POST", body: JSON.stringify(sub) }),
  pushUnsubscribe: (endpoint: string) =>
    req<{ ok: boolean; removed: boolean }>("/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint }) }),
  pushTest: () =>
    req<{ ok: boolean; sent: number; removed: number; failed: number }>("/push/test", { method: "POST" }),
  sendCommand: (id: string, command: string, action: "on" | "off") =>
    req<CommandRow>(`${t(id)}/commands`, { method: "POST", body: JSON.stringify({ command, action }) }),
};
