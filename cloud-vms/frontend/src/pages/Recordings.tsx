import { useState } from "react";
import { errorText, get } from "../api";
import { Empty, ErrorBox, useToast } from "../components/ui";
import { fmtBytes, fmtDuration, localInputValue, useLoad } from "../hooks";
import type { Camera, Recording } from "../types";

export default function Recordings() {
  const toast = useToast();
  const { data: cams } = useLoad(() => get<Camera[]>("/cameras"), []);
  const [camId, setCamId] = useState<number>(0);
  const [day, setDay] = useState(() => localInputValue(new Date()).slice(0, 10));
  const cid = camId || cams?.[0]?.id || 0;
  const start = new Date(`${day}T00:00:00`);
  const end = new Date(start.getTime() + 86400000);
  const { data, error } = useLoad(() => (cid ? get<{ items: Recording[]; total_bytes: number; total_seconds: number; total_segments: number }>(
    `/cameras/${cid}/recordings`, { start: start.toISOString(), end: end.toISOString(), limit: 2000 }) : Promise.resolve(null)), [cid, day], 15000);
  const [playing, setPlaying] = useState<{ rec: Recording; url: string } | null>(null);

  const play = async (r: Recording) => {
    try { const p = await get<{ url: string }>(`/recordings/${r.id}/playback`); setPlaying({ rec: r, url: p.url }); }
    catch (e) { toast(errorText(e), "err"); }
  };
  const byHour = new Map<number, Recording[]>();
  data?.items.forEach((r) => { const h = new Date(r.start_ts).getHours(); byHour.set(h, [...(byHour.get(h) || []), r]); });

  return (
    <>
      <div className="page-head">
        <div><h1>Recordings</h1><p>Continuous footage is stored in fixed-length segments. Pick an hour to play it back.</p></div>
        <span className="spacer" />
        <label className="field">Camera<select value={cid} onChange={(e) => setCamId(+e.target.value)}>{cams?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <label className="field">Day<input type="date" value={day} onChange={(e) => setDay(e.target.value)} /></label>
      </div>
      <ErrorBox error={error} />
      <div className="grid cols-3">
        <section className="panel span-2">
          <div className="body">
            {playing ? (<>
              <video key={playing.url} className="media" src={playing.url} controls autoPlay />
              <p className="small muted" style={{ marginBottom: 0 }}>{new Date(playing.rec.start_ts).toLocaleString()}, {fmtDuration(playing.rec.duration_s)}
                {playing.rec.status === "incomplete" && ", recovered after an interruption"}</p>
            </>) : <Empty title="Choose a segment to play">Segments appear here once a camera has recording switched on.</Empty>}
          </div>
        </section>
        <section className="panel">
          <header><h2>{data ? `${data.items.length} segments` : "Segments"}</h2><span className="spacer" />
            {data && <span className="small muted">{fmtBytes(data.total_bytes)} stored for this camera</span>}</header>
          <div className="body" style={{ maxHeight: 560, overflow: "auto" }}>
            {data && data.items.length === 0 && <Empty title="Nothing recorded on this day" />}
            {[...byHour.entries()].sort((a, b) => b[0] - a[0]).map(([h, recs]) => (
              <div key={h} style={{ marginBottom: 12 }}>
                <h3 className="num">{String(h).padStart(2, "0")}:00</h3>
                <div className="btn-row" style={{ marginTop: 6 }}>
                  {recs.map((r) => (
                    <button key={r.id} className={playing?.rec.id === r.id ? "btn-primary" : ""} onClick={() => play(r)}
                      title={`${fmtDuration(r.duration_s)}, ${fmtBytes(r.size_bytes)}${r.status !== "complete" ? `, ${r.status}` : ""}`}>
                      {new Date(r.start_ts).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}{r.status !== "complete" ? " *" : ""}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </>
  );
}
