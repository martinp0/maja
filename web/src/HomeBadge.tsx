import type { GeofenceState } from "./api";
import { fmtDistance, fmtDuration } from "./format";

/** Řádek pod jménem: Doma / Venku / Utekla a jak dlouho. */
export function HomeBadge({ geo }: { geo: GeofenceState | undefined }) {
  if (!geo || !geo.enabled) return null;
  if (geo.state === "unknown") {
    return (
      <p className="place unknown" title={geo.schema_ready ? "Zatím málo dat" : "Chybí tabulky (init-db)"}>
        Zjišťuji polohu vůči domovu
      </p>
    );
  }
  const kind = geo.state === "home" ? "home" : geo.escaped ? "escaped" : "away";
  const label = kind === "home" ? "Doma" : kind === "escaped" ? "Utekla" : "Venku";
  const dur = fmtDuration(geo.since);
  const dist = geo.state === "away" && geo.distance_m != null ? fmtDistance(geo.distance_m) : "";
  const pending =
    geo.pending_state && geo.pending_state !== geo.state
      ? geo.pending_state === "away"
        ? "možná odchází"
        : "možná se vrací"
      : null;
  return (
    <p className={`place ${kind}`}>
      <span className="place-name">{label}</span>
      {dur && <span> · {dur}</span>}
      {dist && <span> · {dist} od domova</span>}
      {pending && <span className="place-pending"> · {pending}</span>}
    </p>
  );
}
