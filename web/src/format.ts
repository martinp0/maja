const TZ = "Europe/Prague";

const dtFmt = new Intl.DateTimeFormat("cs-CZ", {
  timeZone: TZ,
  day: "numeric",
  month: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const timeFmt = new Intl.DateTimeFormat("cs-CZ", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const dayKey = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });

/** Čas v Europe/Prague; dnešek jen jako HH:MM. */
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return dayKey.format(d) === dayKey.format(new Date()) ? `dnes ${timeFmt.format(d)}` : dtFmt.format(d);
}

/** HH:MM (Europe/Prague); jiný den než dnes s datem: „18. 10. 21:05“. */
export function fmtClock(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return dayKey.format(d) === dayKey.format(new Date()) ? timeFmt.format(d) : dtFmt.format(d);
}

/** Jen hodiny a minuty, bez „dnes“ – když je den už v nadpisu skupiny. */
export function fmtClockOnly(iso: string | null | undefined): string {
  if (!iso) return "–";
  return timeFmt.format(new Date(iso));
}

/** Nadpis dne v historii: Dnes / Včera / „pondělí 20. října“. */
export function dayHeading(iso: string): string {
  const key = dayKey.format(new Date(iso));
  if (key === dayKey.format(new Date())) return "Dnes";
  if (key === dayKey.format(new Date(Date.now() - 86_400_000))) return "Včera";
  return new Intl.DateTimeFormat("cs-CZ", {
    timeZone: TZ,
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(new Date(iso));
}

export function fmtAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const s = Math.round((now - new Date(iso).getTime()) / 1000);
  if (s < 60) return "právě teď";
  const m = Math.round(s / 60);
  if (m < 60) return `před ${m} min`;
  const h = Math.round(m / 60);
  if (h < 48) return `před ${h} h`;
  return `před ${Math.round(h / 24)} dny`;
}

export function fmtMinutes(min: number | null | undefined): string {
  if (min == null) return "–";
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h ? (m ? `${h} h ${m} min` : `${h} h`) : `${m} min`;
}

export function sensorLabel(sensor: string | null | undefined): string {
  switch ((sensor ?? "").toUpperCase()) {
    case "GPS":
      return "GPS";
    case "KNOWN_WIFI":
      return "Domácí WiFi";
    case "WIFI":
      return "WiFi";
    case "BLE":
      return "Bluetooth";
    case "GSM":
    case "CELL":
      return "Mobilní síť";
    case "":
      return "–";
    default:
      return sensor!;
  }
}

export function stateLabel(state: string | null | undefined, reason: string | null | undefined, powerSaving?: boolean | null): string {
  if (powerSaving || reason === "POWER_SAVING") return "Úsporný režim (doma)";
  switch (state) {
    case "OPERATIONAL":
      return "V provozu";
    case "CHARGING":
      return "Nabíjí se";
    case null:
    case undefined:
      return "–";
    default:
      return state;
  }
}

export const COMMAND_LABEL: Record<string, string> = { live: "LIVE", buzzer: "Zvuk", led: "Světlo" };

export const STATUS_LABEL: Record<string, string> = {
  queued: "Ve frontě",
  sending: "Odesílá se",
  sent: "Odesláno",
  done: "Hotovo",
  deferred: "Odloženo",
  failed: "Selhalo",
};

export function errorLabel(err: string | null | undefined): string | null {
  if (!err) return null;
  if (err === "tracker_in_home_zone") return "Mája je doma – čeká se, až bude venku";
  if (err === "expired") return "Vypršelo";
  if (err === "unconfirmed") return "Bez potvrzení z trackeru";
  if (err === "interrupted") return "Přerušeno restartem, zkusí se znovu";
  if (err.startsWith("max_attempts")) return "Vyčerpány pokusy";
  return err;
}

/** Délka trvání od `iso` do teď česky: „45 s“, „15 min“, „2 h 10 min“, „3 dny“. */
export function fmtDuration(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 && h < 10 ? `${h} h ${m % 60} min` : `${h} h`;
  const d = Math.floor(h / 24);
  return d === 1 ? "1 den" : d < 5 ? `${d} dny` : `${d} dní`;
}

export function fmtDistance(m: number | null | undefined): string {
  if (m == null) return "–";
  return m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(m < 10_000 ? 1 : 0).replace(".", ",")} km`;
}
