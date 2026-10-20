import type { Health } from "./api";
import { fmtMinutes, fmtTime } from "./format";

function Ring({ value, goal }: { value: number; goal: number }) {
  const r = 36;
  const c = 2 * Math.PI * r;
  const pct = goal > 0 ? Math.min(value / goal, 1) : 0;
  return (
    <svg viewBox="0 0 88 88" className="ring" role="img" aria-label={`${value} z ${goal} minut`}>
      <circle cx="44" cy="44" r={r} className="ring-bg" />
      <circle
        cx="44"
        cy="44"
        r={r}
        className={`ring-fg ${pct >= 1 ? "done" : ""}`}
        strokeDasharray={`${c * pct} ${c}`}
        transform="rotate(-90 44 44)"
      />
      <text x="44" y="42" textAnchor="middle" className="ring-value">
        {value}
      </text>
      <text x="44" y="56" textAnchor="middle" className="ring-goal">
        z {goal} min
      </text>
    </svg>
  );
}

export function ActivityCard({ health }: { health: Health | undefined }) {
  const d = health?.latest;
  const goal = d?.minutes_goal ?? health?.daily_active_minutes_goal ?? 0;
  const active = d?.minutes_active ?? 0;
  const sleepTotal = (d?.sleep_day_min ?? 0) + (d?.sleep_night_min ?? 0);
  const hasSleep = d && (d.sleep_day_min != null || d.sleep_night_min != null);

  return (
    <section className="block activity">
      <h2>
        Aktivita {d ? <span className="muted">· {d.day}</span> : null}
      </h2>
      {!d ? (
        <p className="empty">Dnešní aktivita se ještě nestáhla.</p>
      ) : (
        <div className="activity-body">
          <Ring value={active} goal={goal} />
          <div className="activity-stats">
            <div>
              <div className="label">Aktivní</div>
              <div className="stat-value">
                {fmtMinutes(d.minutes_active)}
                {goal ? <span className="muted"> / {fmtMinutes(goal)}</span> : null}
              </div>
            </div>
            {hasSleep && (
              <div>
                <div className="label">Spánek</div>
                <div className="stat-value">{fmtMinutes(sleepTotal)}</div>
                <div className="muted">
                  noc {fmtMinutes(d.sleep_night_min)} · den {fmtMinutes(d.sleep_day_min)}
                  {d.sleep_calm_min != null ? ` · klid ${fmtMinutes(d.sleep_calm_min)}` : ""}
                </div>
              </div>
            )}
            {d.synced_at && <div className="muted">synchronizováno {fmtTime(d.synced_at)}</div>}
          </div>
        </div>
      )}
    </section>
  );
}
