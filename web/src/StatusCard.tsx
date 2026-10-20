import { useState } from "react";
import { api, type CommandRow, type Latest } from "./api";
import { fmtAgo, fmtClock, sensorLabel, stateLabel, STATUS_LABEL } from "./format";
import { tick } from "./haptic";
import { IconBolt, IconLive } from "./icons";

interface Props {
  latest: Latest | undefined;
  petName: string;
  trackerId?: string;
  commands?: CommandRow[];
  onSent?: () => void;
}

const OPEN = new Set(["queued", "sending", "sent", "deferred"]);

/** LIVE aktivní podle posledního stavu ze streamu (trackers.last_switches.live_tracking). */
export function liveActive(latest: Latest | undefined, now = Date.now()): boolean {
  const sw = latest?.tracker?.last_switches?.live_tracking;
  if (!sw?.active) return false;
  if (sw.at && sw.remaining != null) return (sw.at + sw.remaining) * 1000 > now;
  return true;
}

/** Souhrn v hlavičce listu: čas, baterie, spojení. Bez tlačítek, ať se za něj dá táhnout. */
export function StatusGlance({ latest }: { latest: Latest | undefined }) {
  const pos = latest?.position;
  const hw = latest?.hw;
  const battery = hw?.battery_pct ?? null;
  const charging = hw?.charging || hw?.charging_state === "CHARGING";
  const batteryClass = battery == null ? "" : battery <= 15 ? "bad" : battery <= 30 ? "warn" : "";
  const powerSaving = !!(hw?.power_saving || latest?.tracker?.state_reason === "POWER_SAVING");
  const onWifi = (pos?.sensor_used ?? "").toUpperCase() === "KNOWN_WIFI";
  const contactTs = latest?.tracker?.updated_at ?? hw?.ts ?? null;
  const posAgeMin = pos ? (Date.now() - Date.parse(pos.ts)) / 60_000 : null;
  const stale = posAgeMin != null && posAgeMin > 30;
  const accuracy = pos?.accuracy_m != null ? ` · ± ${Math.round(pos.accuracy_m)} m` : "";

  return (
    <div className="glance">
      <p className={`seen-time ${stale ? "stale" : ""}`}>{pos ? fmtClock(pos.ts) : "Zatím žádná poloha"}</p>
      <p className="seen-meta">
        {pos ? (
          <>
            {fmtAgo(pos.ts)} · {sensorLabel(pos.sensor_used)}
            {accuracy}
          </>
        ) : (
          "Poloha se objeví, jakmile obojek pošle první bod."
        )}
      </p>
      {powerSaving && onWifi && (
        <p className="quiet-note">Doma na Wi‑Fi. Obojek šetří baterii a polohu posílá zřídka.</p>
      )}
      <div className="stat-row">
        <div>
          <span className="label">Baterie</span>
          <strong className={batteryClass}>
            {battery == null ? "–" : `${battery} %`}
            {charging && (
              <span className="bolt" title="Nabíjí se">
                <IconBolt />
              </span>
            )}
          </strong>
          {battery != null && (
            <span className="bar" aria-hidden>
              <span className={`bar-fill ${batteryClass}`} style={{ width: `${battery}%` }} />
            </span>
          )}
        </div>
        <div>
          <span className="label">Spojení</span>
          <strong>{contactTs ? fmtAgo(contactTs) : "–"}</strong>
          <span className="sub">{contactTs ? fmtClock(contactTs) : "obojek se neozval"}</span>
        </div>
        <div>
          <span className="label">Obojek</span>
          <strong className="compact">
            {stateLabel(hw?.tracker_state ?? latest?.tracker?.state, latest?.tracker?.state_reason, hw?.power_saving)}
          </strong>
        </div>
      </div>
    </div>
  );
}

export function LiveAction({ latest, petName, trackerId, commands, onSent }: Props) {
  const live = liveActive(latest);
  const openLive = commands?.find((c) => c.command === "live" && c.action === "on" && OPEN.has(c.status));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "info"; text: string } | null>(null);

  const startLive = async () => {
    if (!trackerId || busy) return;
    tick(12);
    setBusy(true);
    setMsg(null);
    try {
      const row = await api.sendCommand(trackerId, "live", "on");
      setMsg(
        row.in_home_zone
          ? { kind: "info", text: `${petName} je doma na Wi‑Fi. Živá poloha se zapne, až obojek vyjde ven.` }
          : { kind: "ok", text: "Zapínám živou polohu. První body dorazí za pár sekund." },
      );
      onSent?.();
    } catch (e) {
      setMsg({ kind: "err", text: `Nepovedlo se: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  };

  if (!trackerId) return null;

  return (
    <div className="live-block">
      {live ? (
        <div className="live-status">
          <span className="live-dot" />
          Živá poloha běží
        </div>
      ) : (
        <button type="button" className="btn primary live-btn" onClick={startLive} disabled={busy || !!openLive}>
          <IconLive />
          {openLive ? `Živá poloha · ${STATUS_LABEL[openLive.status] ?? openLive.status}` : busy ? "Zapínám…" : "Živá poloha"}
        </button>
      )}
      {msg && <p className={`msg ${msg.kind}`}>{msg.text}</p>}
    </div>
  );
}
