import type { GeofenceEvent, GeofenceState } from "./api";
import { dayHeading, fmtClockOnly, fmtDistance } from "./format";

/** Historie odchodů a návratů, seskupená po dnech. */
export function GeofenceHistory({ events, geo }: { events: GeofenceEvent[] | undefined; geo: GeofenceState | undefined }) {
  if (!geo?.enabled) return null;
  const list = events ?? [];

  const groups: { label: string; rows: { e: GeofenceEvent; i: number }[] }[] = [];
  list.forEach((e, i) => {
    const label = dayHeading(e.happened_at);
    const last = groups[groups.length - 1];
    if (!last || last.label !== label) groups.push({ label, rows: [{ e, i }] });
    else last.rows.push({ e, i });
  });

  return (
    <section className="block">
      <h2>Pohyb kolem domova</h2>
      {list.length === 0 ? (
        <p className="empty">Zatím žádný odchod ani návrat.</p>
      ) : (
        groups.map((g) => (
          <div key={g.label} className="day-group">
            <h3>{g.label}</h3>
            <ol className="timeline">
              {g.rows.map(({ e, i }) => {
                const left = e.event_type === "left_home";
                const prev = list[i + 1];
                let detail = "";
                if (left && e.distance_m != null) detail = fmtDistance(e.distance_m);
                else if (!left && prev?.event_type === "left_home") {
                  const min = Math.max(0, Math.round((Date.parse(e.happened_at) - Date.parse(prev.happened_at)) / 60000));
                  detail = min < 60 ? `${min} min venku` : `${Math.floor(min / 60)} h ${min % 60} min venku`;
                }
                return (
                  <li key={e.id} className={left ? "left" : "back"}>
                    <span className="time">{fmtClockOnly(e.happened_at)}</span>
                    <span className="rail" aria-hidden />
                    <span className="what">
                      <span className="what-name">{left ? "Odešla" : "Vrátila se"}</span>
                      {detail && <span className="what-detail">{detail}</span>}
                    </span>
                  </li>
                );
              })}
            </ol>
          </div>
        ))
      )}
    </section>
  );
}
