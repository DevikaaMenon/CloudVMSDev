import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "../api";
import { useAuth } from "../auth";
import { Empty, ErrorBox, StatusBadge, useToast } from "../components/ui";
import { fmtAgo, useLoad } from "../hooks";
import type { Camera } from "../types";

export default function Live() {
  const { can } = useAuth();
  const { data: cams, error, reload } = useLoad(() => get<Camera[]>("/cameras"), [], 5000);
  const [focus, setFocus] = useState<number | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (!cams) return <p className="muted">Loading cameras</p>;
  const shown = focus ? cams.filter((c) => c.id === focus) : cams;
  return (
    <>
      <div className="page-head">
        <div><h1>Live view</h1><p>Boxes, zones and counts are drawn by the analytics worker on the frames it analysed.</p></div>
        <span className="spacer" />
        {focus && <button onClick={() => setFocus(null)}>Show all cameras</button>}
      </div>
      {cams.length === 0 ? (
        <div className="panel"><Empty title="No cameras to watch yet">{can("cameras:manage") && <Link to="/cameras">Add a camera</Link>}</Empty></div>
      ) : (
        <div className="live-grid" style={focus ? { gridTemplateColumns: "1fr" } : undefined}>
          {shown.map((c) => <LiveTile key={c.id} cam={c} onFocus={() => setFocus(focus ? null : c.id)} canControl={can("streams:control")} onChanged={reload} />)}
        </div>
      )}
    </>
  );
}

function LiveTile({ cam, onFocus, canControl, onChanged }: { cam: Camera; onFocus: () => void; canControl: boolean; onChanged: () => void }) {
  const [src, setSrc] = useState<string | null>(null);
  const [mode, setMode] = useState<"analytics" | "hls">("analytics");
  const [liveUrl, setLiveUrl] = useState<string | null>(null);
  const toast = useToast();
  const imgRef = useRef<HTMLImageElement>(null);

  const connect = async () => {
    try {
      const r = await post<{ mjpeg_url: string; live_url: string | null }>(`/cameras/${cam.id}/live-token`);
      setSrc(`${r.mjpeg_url}&t=${Date.now()}`);
      setLiveUrl(r.live_url);
    } catch { setSrc(null); }
  };
  useEffect(() => { if (cam.enabled) connect(); else setSrc(null); /* eslint-disable-next-line */ }, [cam.id, cam.enabled]);

  const toggle = async () => {
    try {
      await post(`/cameras/${cam.id}/stream/${cam.enabled ? "stop" : "start"}`);
      toast(cam.enabled ? `Stopped ${cam.name}` : `Starting ${cam.name}`);
      onChanged();
    } catch (e) { toast(String((e as Error).message), "err"); }
  };

  return (
    <article className="live-tile">
      <div className="frame">
        {!cam.enabled ? <span>Stream stopped</span>
          : mode === "hls" && liveUrl ? <HlsPlayer url={liveUrl} />
          : src ? <img ref={imgRef} src={src} alt={`Live view of ${cam.name}`} onError={() => setTimeout(connect, 3000)} />
          : <span>Connecting</span>}
      </div>
      <footer>
        <b>{cam.name}</b><StatusBadge status={cam.enabled ? cam.status : "DISABLED"} />
        <span className="small muted">{cam.status === "ONLINE" ? "" : cam.status_message || (cam.last_seen_at ? `last frame ${fmtAgo(cam.last_seen_at)}` : "")}</span>
        <span className="spacer" />
        {liveUrl && cam.enabled && <button className="btn-quiet" onClick={() => setMode(mode === "hls" ? "analytics" : "hls")}>{mode === "hls" ? "Analytics view" : "Raw stream"}</button>}
        <button className="btn-quiet" onClick={onFocus}>Enlarge</button>
        {canControl && <button onClick={toggle}>{cam.enabled ? "Stop" : "Start"}</button>}
      </footer>
    </article>
  );
}

function HlsPlayer({ url }: { url: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    if (v.canPlayType("application/vnd.apple.mpegurl")) { v.src = url; return; }
    let hls: any;
    import("hls.js").then(({ default: Hls }) => {
      if (Hls.isSupported()) { hls = new Hls({ lowLatencyMode: true }); hls.loadSource(url); hls.attachMedia(v); }
    });
    return () => hls?.destroy();
  }, [url]);
  return <video ref={ref} autoPlay muted playsInline />;
}
