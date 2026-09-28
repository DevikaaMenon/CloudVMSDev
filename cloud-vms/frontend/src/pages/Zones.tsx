import { useEffect, useMemo, useRef, useState, type PointerEvent as RPE } from "react";
import { useSearchParams } from "react-router-dom";
import { del, errorText, get, patch, post } from "../api";
import { useAuth } from "../auth";
import { Empty, ErrorBox, useToast } from "../components/ui";
import { classLabel, useLoad } from "../hooks";
import type { Camera, Policy, ScheduleWindow, Severity, Zone, ZoneType } from "../types";

const COLORS: Record<string, string> = {
  intrusion: "#d8413a", restricted: "#e8903d", authorized: "#3f9b5c", monitoring: "#3c7fc4", counting: "#9c5ec8", line: "#f2c230",
};
const TYPE_TEXT: Record<ZoneType, string> = {
  line: "Counting line (entries / exits)",
  intrusion: "Intrusion area (perimeter, protected hours)",
  restricted: "Restricted area (who may enter, and when)",
  monitoring: "Monitoring area (loitering, crowding)",
  counting: "Occupancy area",
  authorized: "Authorised area (informational)",
};
const OBJECT_OPTIONS = ["person", "vehicle", "two_wheeler", "three_wheeler", "car", "bus", "truck", "van", "lcv", "bicycle"];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

type Draft = Omit<Zone, "id" | "camera_id" | "version" | "created_at" | "updated_at" | "shape" | "geometry"> & {
  id?: number; points: [number, number][];
};

function preset(t: ZoneType): Draft {
  const base = { name: "", zone_type: t, enabled: true, severity: "medium" as Severity, default_action: "allow" as const,
    config: {} as Record<string, unknown>, policies: [] as Policy[], points: [] as [number, number][] };
  if (t === "line") return { ...base, name: "Gate line", config: { in_side: "positive" } };
  if (t === "intrusion") return { ...base, name: "Perimeter", severity: "high", config: { require_entry_transition: true, min_persistence: 2 },
    policies: [{ name: "Closed at night", object_types: ["person", "vehicle"], schedule: [{ days: [0, 1, 2, 3, 4, 5, 6], start: "20:00", end: "06:00" }], action: "alert", severity: null, enabled: true }] };
  if (t === "restricted") return { ...base, name: "Footpath", severity: "medium",
    policies: [{ name: "No vehicles", object_types: ["vehicle"], schedule: [], action: "alert", severity: null, enabled: true }] };
  if (t === "monitoring") return { ...base, name: "Waiting area", severity: "low", config: { dwell_seconds: 120 } };
  return { ...base, name: t === "counting" ? "Occupancy area" : "Authorised area" };
}

export default function Zones() {
  const { can } = useAuth();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const { data: cams } = useLoad(() => get<Camera[]>("/cameras"), []);
  const camId = Number(params.get("camera")) || cams?.[0]?.id || 0;
  const { data: zones, reload, error } = useLoad(() => (camId ? get<Zone[]>(`/cameras/${camId}/zones`) : Promise.resolve([])), [camId]);
  const { data: snap, error: snapErr, reload: reloadSnap } = useLoad(
    () => (camId ? get<{ url: string; width: number; height: number }>(`/cameras/${camId}/snapshot`) : Promise.resolve(null)), [camId]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [saveErr, setSaveErr] = useState<unknown>(null);
  const manage = can("zones:manage");

  useEffect(() => { setDraft(null); setDrawing(false); }, [camId]);

  const select = (z: Zone) => {
    setDraft({ ...z, points: z.geometry.points, policies: z.policies.map((p) => ({ ...p })) });
    setDrawing(false);
    setSaveErr(null);
  };
  const startNew = (t: ZoneType) => { setDraft(preset(t)); setDrawing(true); setSaveErr(null); };

  const save = async () => {
    if (!draft) return;
    setSaveErr(null);
    const body = { name: draft.name, zone_type: draft.zone_type, points: draft.points, enabled: draft.enabled, severity: draft.severity,
      default_action: draft.default_action, config: draft.config,
      policies: draft.zone_type === "line" ? [] : draft.policies.map(({ id: _id, ...p }) => p) };
    try {
      const z = draft.id ? await patch<Zone>(`/zones/${draft.id}`, body) : await post<Zone>(`/cameras/${camId}/zones`, body);
      toast(draft.id ? "Zone saved" : "Zone created");
      await reload();
      select(z);
    } catch (e) { setSaveErr(e); }
  };
  const remove = async () => {
    if (!draft?.id || !confirm(`Delete "${draft.name}"?`)) return;
    try { await del(`/zones/${draft.id}`); setDraft(null); reload(); toast("Zone deleted"); } catch (e) { toast(errorText(e), "err"); }
  };

  if (cams && cams.length === 0) return <div className="panel"><Empty title="Add a camera first">Zones are drawn on a camera's picture.</Empty></div>;

  return (
    <>
      <div className="page-head">
        <div><h1>Zones & rules</h1><p>Draw on the camera picture. Objects are placed by the point where they touch the ground.</p></div>
        <span className="spacer" />
        <label className="field" style={{ minWidth: 220 }}>Camera
          <select value={camId} onChange={(e) => setParams({ camera: e.target.value })}>
            {cams?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <button onClick={() => reloadSnap()}>Refresh picture</button>
      </div>
      <div className="zone-layout">
        <div>
          <ZoneCanvas imgUrl={snap?.url} zones={zones || []} draft={draft} drawing={drawing}
            onChange={(pts) => setDraft((d) => (d ? { ...d, points: pts } : d))}
            onDone={() => setDrawing(false)} onPick={(z) => select(z)} />
          <ErrorBox error={snapErr} />
          {drawing && draft && <p className="hint" style={{ marginTop: 8 }}>
            {draft.zone_type === "line" ? "Click two points across the gate. The arrow shows the entry direction; flip it on the right if needed."
              : "Click to place corners. Click the first corner (or press Finish) to close the shape. Drag corners to adjust."}
            {" "}<button className="btn-quiet" onClick={() => setDrawing(false)} disabled={draft.points.length < (draft.zone_type === "line" ? 2 : 3)}>Finish</button>
            <button className="btn-quiet" onClick={() => setDraft({ ...draft, points: draft.points.slice(0, -1) })}>Undo point</button>
          </p>}
        </div>

        <aside className="grid" style={{ gap: 16 }}>
          <section className="panel">
            <header><h2>Zones on this camera</h2></header>
            <div className="body" style={{ paddingTop: 8 }}>
              <ErrorBox error={error} />
              {zones?.length === 0 && <p className="muted small" style={{ marginTop: 0 }}>None yet. Start with a counting line across the gate.</p>}
              {zones?.map((z) => (
                <div key={z.id} className={`zone-item ${draft?.id === z.id ? "sel" : ""}`} onClick={() => select(z)}>
                  <span className="swatch" style={{ background: COLORS[z.zone_type] }} />
                  <div style={{ flex: 1 }}><b>{z.name}</b><div className="small muted">{TYPE_TEXT[z.zone_type].split(" (")[0]}{z.enabled ? "" : ", disabled"}</div></div>
                </div>
              ))}
              {manage && (
                <label className="field" style={{ marginTop: 10 }}>Add
                  <select value="" onChange={(e) => e.target.value && startNew(e.target.value as ZoneType)}>
                    <option value="">Choose what to draw</option>
                    {(Object.keys(TYPE_TEXT) as ZoneType[]).map((t) => <option key={t} value={t}>{TYPE_TEXT[t]}</option>)}
                  </select>
                </label>
              )}
            </div>
          </section>
          {draft && <ZoneForm draft={draft} setDraft={setDraft} readOnly={!manage} onSave={save} onDelete={remove} error={saveErr} />}
        </aside>
      </div>
    </>
  );
}

// -------------------------------------------------------------------- canvas
function ZoneCanvas({ imgUrl, zones, draft, drawing, onChange, onDone, onPick }: {
  imgUrl?: string; zones: Zone[]; draft: Draft | null; drawing: boolean;
  onChange: (p: [number, number][]) => void; onDone: () => void; onPick: (z: Zone) => void;
}) {
  const [size, setSize] = useState({ w: 1600, h: 900 });
  const svgRef = useRef<SVGSVGElement>(null);
  const dragIdx = useRef<number | null>(null);
  const W = 1000, H = (1000 * size.h) / size.w;
  const toNorm = (e: { clientX: number; clientY: number }): [number, number] => {
    const r = svgRef.current!.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  const pts = (p: [number, number][]) => p.map(([x, y]) => `${x * W},${y * H}`).join(" ");
  const isLine = draft?.zone_type === "line";

  const onDown = (e: RPE<SVGSVGElement>) => {
    if (!draft || !drawing) return;
    const p = toNorm(e);
    const first = draft.points[0];
    if (!isLine && draft.points.length >= 3 && first && Math.hypot((p[0] - first[0]) * W, (p[1] - first[1]) * H) < 14) { onDone(); return; }
    const next = [...draft.points, p];
    onChange(next);
    if (isLine && next.length >= 2) onDone();
  };
  const onMove = (e: RPE<SVGSVGElement>) => {
    if (dragIdx.current === null || !draft) return;
    const next = draft.points.slice();
    next[dragIdx.current] = toNorm(e);
    onChange(next);
  };

  const arrow = (p: [number, number][], side: string) => {
    if (p.length < 2) return null;
    const [a, b] = [[p[0][0] * W, p[0][1] * H], [p[1][0] * W, p[1][1] * H]];
    const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, dx = b[0] - a[0], dy = b[1] - a[1], n = Math.hypot(dx, dy) || 1;
    const s = side === "negative" ? -1 : 1;
    const ex = mx + (-dy / n) * 46 * s, ey = my + (dx / n) * 46 * s;
    return <g><line x1={mx} y1={my} x2={ex} y2={ey} stroke="#f2c230" strokeWidth={4} markerEnd="url(#arrow)" /><text x={ex + 6} y={ey} fill="#fff" fontSize={16} stroke="#000" strokeWidth={3} paintOrder="stroke">in</text></g>;
  };

  return (
    <div className={`canvas-wrap ${drawing ? "drawing" : ""}`}>
      {imgUrl ? <img src={imgUrl} alt="Camera picture for drawing zones" onLoad={(e) => setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} />
        : <div style={{ aspectRatio: "16/9", display: "grid", placeItems: "center", color: "#7f8aa3" }}>Waiting for a picture from the camera</div>}
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={() => (dragIdx.current = null)}
        onPointerLeave={() => (dragIdx.current = null)}>
        <defs><marker id="arrow" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#f2c230" /></marker></defs>
        {zones.filter((z) => z.id !== draft?.id).map((z) => z.shape === "line" ? (
          <g key={z.id} onPointerDown={(e) => { if (!drawing) { e.stopPropagation(); onPick(z); } }} style={{ cursor: "pointer" }}>
            <polyline points={pts(z.geometry.points)} stroke={COLORS.line} strokeWidth={5} fill="none" opacity={z.enabled ? 0.9 : 0.4} />
            {arrow(z.geometry.points, String(z.config.in_side || "positive"))}
          </g>
        ) : (
          <polygon key={z.id} points={pts(z.geometry.points)} fill={COLORS[z.zone_type]} fillOpacity={0.18} stroke={COLORS[z.zone_type]}
            strokeWidth={3} opacity={z.enabled ? 1 : 0.4} style={{ cursor: "pointer" }}
            onPointerDown={(e) => { if (!drawing) { e.stopPropagation(); onPick(z); } }} />
        ))}
        {draft && draft.points.length > 0 && (
          <g>
            {isLine ? <polyline points={pts(draft.points)} stroke={COLORS.line} strokeWidth={6} fill="none" />
              : drawing ? <polyline points={pts(draft.points)} stroke={COLORS[draft.zone_type]} strokeWidth={3} fill={COLORS[draft.zone_type]} fillOpacity={0.15} strokeDasharray="8 6" />
              : <polygon points={pts(draft.points)} fill={COLORS[draft.zone_type]} fillOpacity={0.25} stroke="#fff" strokeWidth={3} />}
            {isLine && arrow(draft.points, String(draft.config.in_side || "positive"))}
            {draft.points.map(([x, y], i) => (
              <circle key={i} cx={x * W} cy={y * H} r={i === 0 && drawing && !isLine ? 10 : 7} fill="#fff" stroke="#14213d" strokeWidth={2.5}
                style={{ cursor: "grab" }} onPointerDown={(e) => { if (!drawing) { e.stopPropagation(); dragIdx.current = i; (e.target as Element).setPointerCapture?.(e.pointerId); } }} />
            ))}
          </g>
        )}
      </svg>
    </div>
  );
}

// -------------------------------------------------------------------- form
function ZoneForm({ draft, setDraft, readOnly, onSave, onDelete, error }: {
  draft: Draft; setDraft: (d: Draft) => void; readOnly: boolean; onSave: () => void; onDelete: () => void; error: unknown;
}) {
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft({ ...draft, [k]: v });
  const setCfg = (k: string, v: unknown) => {
    const c = { ...draft.config };
    if (v === "" || v === undefined || v === null) delete c[k]; else c[k] = v;
    set("config", c);
  };
  const isLine = draft.zone_type === "line";
  const minPts = isLine ? 2 : 3;
  const setPolicy = (i: number, p: Partial<Policy>) => set("policies", draft.policies.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const summary = useMemo(() => describe(draft), [draft]);

  return (
    <section className="panel">
      <header><span className="swatch" style={{ background: COLORS[draft.zone_type] }} /><h2>{draft.id ? "Edit zone" : "New zone"}</h2></header>
      <div className="body grid" style={{ gap: 12 }}>
        <fieldset disabled={readOnly} style={{ border: 0, padding: 0, display: "grid", gap: 12 }}>
          <label className="field">Name<input value={draft.name} onChange={(e) => set("name", e.target.value)} /></label>
          <div className="form-grid">
            {!isLine && <label className="field">Kind
              <select value={draft.zone_type} onChange={(e) => set("zone_type", e.target.value as ZoneType)}>
                {(["intrusion", "restricted", "monitoring", "counting", "authorized"] as ZoneType[]).map((t) => <option key={t} value={t}>{TYPE_TEXT[t].split(" (")[0]}</option>)}
              </select></label>}
            {!isLine && <label className="field">Severity
              <select value={draft.severity} onChange={(e) => set("severity", e.target.value as Severity)}>
                {["low", "medium", "high", "critical"].map((s) => <option key={s}>{s}</option>)}
              </select></label>}
            <label className="check full"><input type="checkbox" checked={draft.enabled} onChange={(e) => set("enabled", e.target.checked)} />Active</label>
          </div>

          {isLine ? (
            <div className="grid" style={{ gap: 8 }}>
              <button onClick={() => setCfg("in_side", draft.config.in_side === "negative" ? "positive" : "negative")}>Flip entry direction</button>
              <label className="field">Count only
                <select value={((draft.config.count_classes as string[]) || [])[0] || ""} onChange={(e) => setCfg("count_classes", e.target.value ? [e.target.value] : undefined)}>
                  <option value="">People and vehicles</option><option value="person">People</option><option value="vehicle">Vehicles</option>
                </select></label>
            </div>
          ) : (<>
            {(draft.zone_type === "intrusion" || draft.zone_type === "restricted") && (<>
              <label className="field">When no rule below matches
                <select value={draft.default_action} onChange={(e) => set("default_action", e.target.value as "alert" | "allow")}>
                  <option value="allow">Allow (only the rules below raise incidents)</option>
                  <option value="alert">Raise an incident for anyone</option>
                </select></label>
              <div className="grid" style={{ gap: 8 }}>
                <h3>Rules</h3>
                {draft.policies.map((p, i) => <PolicyEditor key={i} p={p} onChange={(x) => setPolicy(i, x)} onRemove={() => set("policies", draft.policies.filter((_, j) => j !== i))} />)}
                <button onClick={() => set("policies", [...draft.policies, { name: "", object_types: ["person"], schedule: [], action: "alert", severity: null, enabled: true }])}>Add rule</button>
              </div>
            </>)}
            <details>
              <summary className="small">Detection tuning</summary>
              <div className="form-grid" style={{ marginTop: 10 }}>
                <label className="field">Frames inside before it counts<input type="number" min={1} max={20} value={Number(draft.config.min_persistence ?? 2)} onChange={(e) => setCfg("min_persistence", +e.target.value)} /></label>
                {draft.zone_type === "intrusion" && <label className="check"><input type="checkbox" checked={draft.config.require_entry_transition !== false} onChange={(e) => setCfg("require_entry_transition", e.target.checked)} />Only when crossing in from outside</label>}
                {draft.zone_type === "monitoring" && <label className="field">Loitering after (seconds)<input type="number" min={0} value={Number(draft.config.dwell_seconds ?? 0)} onChange={(e) => setCfg("dwell_seconds", +e.target.value)} /></label>}
                {(draft.zone_type === "monitoring" || draft.zone_type === "counting") && <label className="field">Crowd when at least<input type="number" min={0} value={Number(draft.config.crowd_threshold ?? 0)} onChange={(e) => setCfg("crowd_threshold", +e.target.value)} /></label>}
                <label className="check"><input type="checkbox" checked={!!draft.config.require_motion} onChange={(e) => setCfg("require_motion", e.target.checked || undefined)} />Ignore objects that never move</label>
              </div>
            </details>
          </>)}
        </fieldset>
        <p className="small" style={{ margin: 0, color: "var(--ink-2)" }}>{summary}</p>
        {error ? <ErrorBox error={error} /> : null}
        {!readOnly && (
          <div className="btn-row">
            <button className="btn-primary" onClick={onSave} disabled={draft.points.length < minPts || !draft.name}>{draft.id ? "Save zone" : "Create zone"}</button>
            {draft.id && <button className="btn-quiet btn-danger" onClick={onDelete}>Delete</button>}
            {draft.points.length < minPts && <span className="hint">Draw at least {minPts} points on the picture.</span>}
          </div>
        )}
      </div>
    </section>
  );
}

function PolicyEditor({ p, onChange, onRemove }: { p: Policy; onChange: (x: Partial<Policy>) => void; onRemove: () => void }) {
  const toggleObj = (o: string) => onChange({ object_types: p.object_types.includes(o) ? p.object_types.filter((x) => x !== o) : [...p.object_types, o] });
  const setWin = (i: number, w: Partial<ScheduleWindow>) => onChange({ schedule: p.schedule.map((x, j) => (j === i ? { ...x, ...w } : x)) });
  return (
    <div className="policy">
      <div className="btn-row">
        <select value={p.action} onChange={(e) => onChange({ action: e.target.value as "alert" | "allow" })} aria-label="Action">
          <option value="alert">Raise incident for</option><option value="allow">Always allow</option>
        </select>
        <input value={p.name} placeholder="Rule name" onChange={(e) => onChange({ name: e.target.value })} style={{ flex: 1 }} />
        <button className="btn-quiet btn-danger" onClick={onRemove} aria-label="Remove rule">✕</button>
      </div>
      <div className="btn-row">
        {OBJECT_OPTIONS.map((o) => (
          <label key={o} className="check small"><input type="checkbox" checked={p.object_types.includes(o)} onChange={() => toggleObj(o)} />{o === "vehicle" ? "Any vehicle" : o === "person" ? "People" : classLabel(o)}</label>
        ))}
      </div>
      <div className="grid" style={{ gap: 6 }}>
        {p.schedule.length === 0 && <span className="small muted">At all times.</span>}
        {p.schedule.map((w, i) => (
          <div key={i} className="btn-row small">
            {DAYS.map((d, di) => (
              <label key={d} className="check"><input type="checkbox" checked={w.days.includes(di)}
                onChange={() => setWin(i, { days: w.days.includes(di) ? w.days.filter((x) => x !== di) : [...w.days, di].sort() })} />{d}</label>
            ))}
            <input type="time" value={w.start} onChange={(e) => setWin(i, { start: e.target.value })} aria-label="From" />
            <input type="time" value={w.end} onChange={(e) => setWin(i, { end: e.target.value })} aria-label="Until" />
            <button className="btn-quiet" onClick={() => onChange({ schedule: p.schedule.filter((_, j) => j !== i) })}>Remove</button>
          </div>
        ))}
        <button className="btn-quiet" style={{ justifySelf: "start" }} onClick={() => onChange({ schedule: [...p.schedule, { days: [0, 1, 2, 3, 4, 5, 6], start: "20:00", end: "06:00" }] })}>Limit to certain hours</button>
      </div>
    </div>
  );
}

function describe(d: Draft): string {
  if (d.zone_type === "line") return "Every tracked person or vehicle crossing this line is counted once per direction; these counts feed the gate overview.";
  if (d.zone_type === "monitoring") return d.config.dwell_seconds ? `Raises a loitering incident when someone stays longer than ${d.config.dwell_seconds}s.` : "Tracks occupancy; set a loitering time or crowd size to raise incidents.";
  if (d.zone_type === "counting" || d.zone_type === "authorized") return "Shows how many objects are inside. Does not raise incidents.";
  const alertRules = d.policies.filter((p) => p.enabled && p.action === "alert");
  const who = (p: Policy) => (p.object_types.length ? p.object_types.map((o) => (o === "vehicle" ? "vehicles" : o === "person" ? "people" : classLabel(o).toLowerCase())).join(", ") : "anything");
  const when = (p: Policy) => (p.schedule.length ? ` between ${p.schedule.map((w) => `${w.start} and ${w.end}`).join(" or ")}` : "");
  const kind = d.zone_type === "intrusion" ? "intrusion" : "restricted-area access";
  if (d.default_action === "alert") return `Any object entering raises ${kind === "intrusion" ? "an" : "a"} ${kind} incident, except where a rule allows it.`;
  if (!alertRules.length) return "No rule raises incidents yet. Add a rule, or change what happens when no rule matches.";
  return `Raises ${kind === "intrusion" ? "an" : "a"} ${kind} incident for ${alertRules.map((p) => who(p) + when(p)).join("; ")}.`;
}
