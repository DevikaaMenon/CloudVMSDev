import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { get } from "../api";
import { FlowLegend, GateFlowChart, TypeBars } from "../components/Charts";
import { Empty, ErrorBox, SevBadge, StatusBadge } from "../components/ui";
import { classLabel, eventLabel, fmtBytes, fmtClock, fmtDuration, useLoad } from "../hooks";
import type { Summary } from "../types";

type RangeKey = "today" | "yesterday" | "7d" | "30d";

function rangeFor(k: RangeKey): { start?: string; end?: string; label: string } {
  const now = new Date();
  const sod = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (k === "today") return { label: "Today" };
  if (k === "yesterday") {
    const s = new Date(sod.getTime() - 86400000);
    return { start: s.toISOString(), end: sod.toISOString(), label: "Yesterday" };
  }
  const days = k === "7d" ? 7 : 30;
  return { start: new Date(sod.getTime() - (days - 1) * 86400000).toISOString(), end: now.toISOString(), label: `Last ${days} days` };
}

export default function Dashboard() {
  const [range, setRange] = useState<RangeKey>("today");
  const r = useMemo(() => rangeFor(range), [range]);
  const { data: s, error } = useLoad(() => get<Summary>("/analytics/summary", { start: r.start, end: r.end }), [range], 10000);

  if (error && !s) return <ErrorBox error={error} />;
  if (!s) return <p className="muted">Loading today's numbers</p>;
  const lines = s.counting_lines_configured;
  const hourly = s.range.bucket === "hour";
  const peak = s.peak && (s.peak.person_in + s.peak.vehicle_in) > 0 ? s.peak : null;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Gate overview</h1>
          <p>{r.label}. Counts are unique tracked people and vehicles crossing the gate lines.</p>
        </div>
        <span className="spacer" />
        <div className="btn-row" role="group" aria-label="Period">
          {(["today", "yesterday", "7d", "30d"] as RangeKey[]).map((k) => (
            <button key={k} className={k === range ? "btn-primary" : ""} onClick={() => setRange(k)}>{rangeFor(k).label}</button>
          ))}
        </div>
      </div>

      {!lines && (
        <div className="panel" style={{ marginBottom: 16, borderColor: "var(--amber)" }}>
          <div className="body" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <span>No counting line is drawn yet, so entries and exits can't be counted. The numbers below show unique objects seen instead.</span>
            <Link className="btn btn-attn" to="/zones">Draw a gate line</Link>
          </div>
        </div>
      )}

      <section className="gateboard" aria-label="Gate counts">
        <GateSide who="People" color="var(--people)" main={lines ? s.people.entries : s.people.unique_seen}
          mainLabel={lines ? "entered" : "seen"}
          stats={lines ? [["exited", s.people.exits], ["estimated on site", s.people.on_site_estimate], ["seen by cameras", s.people.unique_seen]]
            : [["seen by cameras", s.people.unique_seen]]} />
        <GateSide who="Vehicles" color="var(--vehicles)" main={lines ? s.vehicles.entries : s.vehicles.unique_seen}
          mainLabel={lines ? "entered" : "seen"}
          stats={lines ? [["exited", s.vehicles.exits], ["estimated on site", s.vehicles.on_site_estimate], ["seen by cameras", s.vehicles.unique_seen]]
            : [["seen by cameras", s.vehicles.unique_seen]]}
          chips={Object.entries(lines && Object.keys(s.vehicles.types_in).length ? s.vehicles.types_in : s.vehicles.types_seen).slice(0, 5)} />
      </section>

      <div className="grid cols-3" style={{ marginTop: 16 }}>
        <section className="panel span-2">
          <header><h2>{hourly ? "Flow through the day" : "Flow by day"}</h2><span className="spacer" /><FlowLegend mode={lines ? "crossings" : "seen"} /></header>
          <div className="body">
            {s.series.length ? <div className="chart-box tall"><GateFlowChart series={s.series} hourly={hourly} mode={lines ? "crossings" : "seen"} /></div>
              : <Empty title="Nothing counted yet">Start a camera and the chart fills in as people and vehicles pass.</Empty>}
            {peak && <p className="small muted" style={{ margin: "8px 0 0" }}>
              Busiest {hourly ? "hour" : "day"}: <b className="num">{hourly ? peak.bucket.slice(11) : peak.bucket}</b> with {peak.person_in} people and {peak.vehicle_in} vehicles in.</p>}
          </div>
        </section>

        <section className="panel">
          <header><h2>Incidents</h2><span className="spacer" /><Link to="/events?status=NEW&status=ACKNOWLEDGED&status=INVESTIGATING" className="small">{s.events.open} open</Link></header>
          <div className="body">
            {s.events.recent.length === 0 ? <Empty title="No incidents in this period" /> : (
              <ul className="incident-list">
                {s.events.recent.slice(0, 7).map((e) => (
                  <li key={e.id}>
                    <span className="bar" style={{ background: `var(--sev-${e.severity})` }} />
                    <div style={{ minWidth: 0 }}>
                      <Link to={`/events?open=${e.id}`}>{eventLabel(e.event_type)}</Link>
                      <div className="small muted" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.camera_name}, {e.title.includes(": ") ? e.title.split(": ").slice(1).join(": ") : e.title}</div>
                    </div>
                    <span className="small muted num">{fmtClock(e.event_timestamp)}</span>
                  </li>
                ))}
              </ul>
            )}
            <div className="small muted" style={{ marginTop: 10 }}>
              {s.events.total} in period{s.events.mean_time_to_ack_s != null && <>, acknowledged after {fmtDuration(s.events.mean_time_to_ack_s)} on average</>}
            </div>
          </div>
        </section>
      </div>

      <div className="grid cols-3" style={{ marginTop: 16 }}>
        <section className="panel">
          <header><h2>Vehicles by type</h2></header>
          <div className="body">
            <TypeBars counts={lines && Object.keys(s.vehicles.types_in).length ? s.vehicles.types_in : s.vehicles.types_seen} />
            {lines && !Object.keys(s.vehicles.types_in).length && Object.keys(s.vehicles.types_seen).length > 0 &&
              <p className="small muted" style={{ margin: "10px 0 0" }}>None crossed a gate line yet; showing vehicles seen by the cameras.</p>}
          </div>
        </section>

        <section className="panel">
          <header><h2>Cameras</h2><span className="spacer" /><span className="small muted">{s.cameras.online} of {s.cameras.total} online</span></header>
          <div className="body" style={{ paddingTop: 6 }}>
            {s.per_camera.length === 0 ? <Empty title="No cameras yet"><Link to="/cameras">Add the first camera</Link></Empty> : (
              <table>
                <thead><tr><th>Camera</th><th>Status</th><th className="num">In</th><th className="num">Out</th></tr></thead>
                <tbody>{s.per_camera.map((c) => (
                  <tr key={c.camera_id}><td>{c.name}</td><td><StatusBadge status={c.status} /></td>
                    <td className="num">{c.person_in + c.vehicle_in}</td><td className="num">{c.person_out + c.vehicle_out}</td></tr>
                ))}</tbody>
              </table>
            )}
          </div>
        </section>

        <section className="panel">
          <header><h2>Processing</h2></header>
          <div className="body">
            <dl className="kv">
              <dt>Stream to decision</dt><dd className="num">{s.processing.avg_end_to_end_ms != null ? `${s.processing.avg_end_to_end_ms} ms` : "no live cameras"}</dd>
              <dt>Model time per frame</dt><dd className="num">{s.processing.avg_inference_ms != null ? `${s.processing.avg_inference_ms} ms` : "—"}</dd>
              <dt>Frames analysed</dt><dd className="num">{s.processing.total_inference_fps} per second</dd>
              <dt>Worker CPU</dt><dd className="num">{s.processing.cpu_percent != null ? `${s.processing.cpu_percent}%` : "—"}</dd>
              <dt>Detector</dt><dd>{s.workers.detector || "—"}{s.workers.device ? ` on ${s.workers.device}` : ""}</dd>
              <dt>Stored video</dt><dd className="num">{fmtBytes(s.storage_bytes)}</dd>
              <dt>Recording</dt><dd>{s.cameras.recording} camera{s.cameras.recording === 1 ? "" : "s"}</dd>
            </dl>
          </div>
        </section>
      </div>

      {Object.keys(s.events.by_type).length > 0 && (
        <section className="panel" style={{ marginTop: 16 }}>
          <header><h2>Incidents by type</h2></header>
          <div className="body stat-row">
            {Object.entries(s.events.by_type).map(([k, v]) => (
              <div className="stat" key={k}><b>{v}</b><span>{eventLabel(k)}</span></div>
            ))}
            {Object.entries(s.events.by_severity).length > 0 && (
              <div className="stat"><div className="btn-row">{Object.entries(s.events.by_severity).map(([k, v]) =>
                <span key={k} style={{ display: "inline-flex", gap: 4, alignItems: "center" }}><SevBadge severity={k} /><b className="num" style={{ fontSize: 15, display: "inline" }}>{v}</b></span>)}</div>
                <span>by severity</span></div>
            )}
          </div>
        </section>
      )}
    </>
  );
}

function GateSide({ who, color, main, mainLabel, stats, chips }:
  { who: string; color: string; main: number; mainLabel: string; stats: [string, number][]; chips?: [string, number][] }) {
  return (
    <div className="gate-side">
      <div className="who"><i style={{ background: color }} />{who}</div>
      <div><div className="big" style={{ color }}>{main}</div><div className="muted small">{mainLabel}</div></div>
      <div className="gate-stats">
        {stats.map(([k, v]) => <div key={k}><b>{v}</b><span>{k}</span></div>)}
      </div>
      {chips && chips.length > 0 && (
        <div className="gate-foot">{chips.map(([k, v]) => <span key={k} className="badge tag-vehicle">{classLabel(k)} {v}</span>)}</div>
      )}
    </div>
  );
}
