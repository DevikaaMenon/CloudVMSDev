import { useState } from "react";
import { errorText, get, post, put } from "../api";
import { useAuth } from "../auth";
import { Dialog, Empty, ErrorBox, useToast } from "../components/ui";
import { fmtTime, useLoad } from "../hooks";
import type { Dataset, ModelVersion, TrainingJob } from "../types";

export default function Models() {
  const { can } = useAuth();
  const toast = useToast();
  const manage = can("models:manage");
  const { data, reload, error } = useLoad(() => get<{ profile: string; models: ModelVersion[]; canonical_classes: string[] }>("/models"), []);
  const { data: datasets, reload: reloadDs } = useLoad(() => (manage ? get<Dataset[]>("/datasets") : Promise.resolve([])), [manage], 5000);
  const { data: jobs, reload: reloadJobs } = useLoad(() => (manage ? get<TrainingJob[]>("/training-jobs") : Promise.resolve([])), [manage], 5000);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [trainOpen, setTrainOpen] = useState<Dataset | null>(null);
  const [logFor, setLogFor] = useState<number | null>(null);

  const setProfile = async (p: string) => {
    try { await put("/models/profile", undefined, { profile: p }); toast("Detector profile changed; workers reload within a few seconds"); reload(); }
    catch (e) { toast(errorText(e), "err"); }
  };
  const activate = async (m: ModelVersion) => {
    try { await post(`/models/${m.id}/activate`); toast(`${m.name} is now the active ${m.role} model`); reload(); }
    catch (e) { toast(errorText(e), "err"); }
  };

  return (
    <>
      <div className="page-head"><div><h1>Models & data</h1><p>Choose which detectors run, and bring in your own labelled college footage to fine-tune them.</p></div></div>
      <ErrorBox error={error} />
      {data && (
        <section className="panel">
          <header><h2>Detector profile</h2></header>
          <div className="body grid cols-2">
            <ProfileCard active={data.profile === "shared"} title="One shared model" onPick={() => setProfile("shared")} disabled={!can("settings:manage")}
              text="A single COCO-trained model finds people and vehicles in one pass. Fastest; the right choice on a laptop CPU." />
            <ProfileCard active={data.profile === "specialized"} title="People model + Indian vehicle model" onPick={() => setProfile("specialized")} disabled={!can("settings:manage")}
              text="Adds the IISc UVH-26 model trained on Bengaluru CCTV, which knows auto-rickshaws, two-wheelers, tempo travellers and more. About twice the compute." />
          </div>
        </section>
      )}

      <section className="panel" style={{ marginTop: 16 }}>
        <header><h2>Registered models</h2><span className="spacer" />{manage && <button onClick={() => setUploadOpen(true)}>Upload weights</button>}</header>
        <div className="body table-wrap">
          <table>
            <thead><tr><th>Model</th><th>Used for</th><th>Trained on</th><th className="num">mAP50</th><th>Weights</th><th /></tr></thead>
            <tbody>{data?.models.map((m) => (
              <tr key={m.id}>
                <td><b>{m.name}</b>{m.notes && <div className="small muted">{m.notes}</div>}</td>
                <td>{m.role === "shared" ? "people and vehicles" : m.role === "person" ? "people" : "vehicles"}</td>
                <td className="small">{m.dataset || "—"}<div className="muted">{m.source}</div></td>
                <td className="num">{m.metrics?.mAP50 ?? "—"}</td>
                <td className="small">{m.available ? <span className="badge st-ONLINE"><i />ready</span> : <span className="badge st-OFFLINE"><i />missing</span>}
                  {!m.available && m.name.includes("uvh26") && <div className="muted">python ml/datasets/download_models.py --uvh26</div>}</td>
                <td style={{ textAlign: "right" }}>{m.is_active ? <span className="badge" title={m.available ? "" : "Weights missing: another model of this kind is used until they are downloaded"}>{m.available ? "active" : "active, waiting for weights"}</span> : manage && <button disabled={!m.available} onClick={() => activate(m)}>Use this model</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </section>

      {manage && (<>
        <section className="panel" style={{ marginTop: 16 }}>
          <header><h2>Your datasets</h2><span className="spacer" /><UploadDataset onDone={reloadDs} /></header>
          <div className="body">
            <ol className="small" style={{ marginTop: 0, color: "var(--ink-2)", paddingLeft: 18 }}>
              <li>Extract frames from the gate videos: <code>python ml/college/import_college.py frames --videos &lt;folder&gt; --out data/datasets/college_raw</code></li>
              <li>Optionally pre-label them with the current model (<code>autolabel</code>), then correct the boxes in CVAT, Label Studio or Roboflow using the classes {data?.canonical_classes.join(", ")}.</li>
              <li>Export in YOLO format, zip it and upload it here. Frames are split into training and validation by source video.</li>
            </ol>
            {datasets && datasets.length === 0 ? <Empty title="No datasets uploaded yet" /> : (
              <table>
                <thead><tr><th>Dataset</th><th>Status</th><th className="num">Images</th><th>Boxes per class</th><th /></tr></thead>
                <tbody>{datasets?.map((d) => (
                  <tr key={d.id}><td><b>{d.name}</b><div className="small muted">{fmtTime(d.created_at)}</div></td>
                    <td>{d.status}{d.status === "failed" && <div className="small muted" style={{ maxWidth: 360 }}>{String(d.stats.error || "").slice(-240)}</div>}</td>
                    <td className="num">{d.stats.images ?? "—"}</td>
                    <td className="small">{d.stats.boxes_per_class ? Object.entries(d.stats.boxes_per_class).map(([k, v]) => `${k} ${v}`).join(", ") : "—"}
                      {d.stats.split_by && <div className="muted">split by {d.stats.split_by}</div>}</td>
                    <td style={{ textAlign: "right" }}>{d.status === "ready" && <button onClick={() => setTrainOpen(d)}>Fine-tune</button>}</td></tr>
                ))}</tbody>
              </table>
            )}
          </div>
        </section>

        <section className="panel" style={{ marginTop: 16 }}>
          <header><h2>Training jobs</h2></header>
          <div className="body">
            <p className="small muted" style={{ marginTop: 0 }}>Training on a CPU is slow. For real runs use <code>ml/training/train.py</code> on Kaggle, Colab or a GPU instance and upload the resulting best.pt above.</p>
            {jobs && jobs.length === 0 ? <Empty title="No training jobs yet" /> : (
              <table>
                <thead><tr><th>Job</th><th>Base model</th><th>Status</th><th className="num">mAP50</th><th className="num">mAP50-95</th><th /></tr></thead>
                <tbody>{jobs?.map((j) => (
                  <tr key={j.id}><td>#{j.id} <span className="small muted">{fmtTime(j.created_at)}</span></td><td className="small">{j.base_model}, {j.params.epochs} epochs</td>
                    <td>{j.status}</td><td className="num">{j.metrics?.mAP50 ?? "—"}</td><td className="num">{j.metrics?.mAP50_95 ?? "—"}</td>
                    <td style={{ textAlign: "right" }}><button className="btn-quiet" onClick={() => setLogFor(j.id)}>Log</button></td></tr>
                ))}</tbody>
              </table>
            )}
          </div>
        </section>
      </>)}
      {uploadOpen && <UploadWeights onClose={() => setUploadOpen(false)} onDone={() => { setUploadOpen(false); reload(); }} />}
      {trainOpen && <TrainDialog ds={trainOpen} onClose={() => setTrainOpen(null)} onDone={() => { setTrainOpen(null); reloadJobs(); }} />}
      {logFor && <LogDialog id={logFor} onClose={() => setLogFor(null)} />}
    </>
  );
}

function ProfileCard({ active, title, text, onPick, disabled }: { active: boolean; title: string; text: string; onPick: () => void; disabled: boolean }) {
  return (
    <div className="panel" style={{ boxShadow: "none", borderColor: active ? "var(--ink)" : "var(--line)", borderWidth: active ? 2 : 1 }}>
      <div className="body">
        <h3>{title}</h3>
        <p className="small" style={{ color: "var(--ink-2)" }}>{text}</p>
        {active ? <span className="badge">in use</span> : <button disabled={disabled} onClick={onPick}>Switch to this</button>}
      </div>
    </div>
  );
}

function UploadDataset({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const upload = async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name || file.name.replace(/\.zip$/i, ""));
    setBusy(true);
    try { await post("/datasets/upload", fd); toast("Uploaded; checking labels and splitting by video"); onDone(); }
    catch (e) { toast(errorText(e), "err"); } finally { setBusy(false); }
  };
  return (
    <div className="btn-row">
      <input placeholder="Dataset name" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 170 }} />
      <label className="btn">{busy ? "Uploading" : "Upload labelled .zip"}<input type="file" accept=".zip" hidden disabled={busy} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>
    </div>
  );
}

function UploadWeights({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState("shared");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file); fd.append("name", name); fd.append("role", role); fd.append("class_map", "");
    setBusy(true);
    try { await post("/models/upload-weights", fd); onDone(); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <Dialog title="Upload model weights" onClose={onClose} footer={<><button onClick={onClose}>Cancel</button><button className="btn-primary" disabled={!file || !name || busy} onClick={save}>{busy ? "Uploading" : "Upload"}</button></>}>
      <div className="form-grid">
        <label className="field full">Ultralytics .pt file<input type="file" accept=".pt" onChange={(e) => setFile(e.target.files?.[0] || null)} /></label>
        <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="college-gate-v1" /></label>
        <label className="field">Used for<select value={role} onChange={(e) => setRole(e.target.value)}><option value="shared">People and vehicles</option><option value="person">People only</option><option value="vehicle">Vehicles only</option></select></label>
        <p className="hint full">Class names are read from the file and matched to person, two_wheeler, car and the other classes automatically.</p>
      </div>
      {err ? <ErrorBox error={err} /> : null}
    </Dialog>
  );
}

function TrainDialog({ ds, onClose, onDone }: { ds: Dataset; onClose: () => void; onDone: () => void }) {
  const [v, setV] = useState({ base_model: "yolo26n.pt", role: "shared", epochs: 30, imgsz: 640, batch: 8 });
  const [err, setErr] = useState<unknown>(null);
  const start = async () => {
    try { await post("/training-jobs", { dataset_id: ds.id, ...v }); onDone(); } catch (e) { setErr(e); }
  };
  return (
    <Dialog title={`Fine-tune on ${ds.name}`} onClose={onClose} footer={<><button onClick={onClose}>Cancel</button><button className="btn-primary" onClick={start}>Start training</button></>}>
      <div className="form-grid">
        <label className="field">Start from<select value={v.base_model} onChange={(e) => setV({ ...v, base_model: e.target.value })}><option>yolo26n.pt</option><option>yolo26s.pt</option></select></label>
        <label className="field">Model will be used for<select value={v.role} onChange={(e) => setV({ ...v, role: e.target.value })}><option value="shared">People and vehicles</option><option value="person">People</option><option value="vehicle">Vehicles</option></select></label>
        <label className="field">Epochs<input type="number" min={1} max={500} value={v.epochs} onChange={(e) => setV({ ...v, epochs: +e.target.value })} /></label>
        <label className="field">Image size<input type="number" min={320} max={1920} step={32} value={v.imgsz} onChange={(e) => setV({ ...v, imgsz: +e.target.value })} /></label>
        <label className="field">Batch<input type="number" min={1} max={128} value={v.batch} onChange={(e) => setV({ ...v, batch: +e.target.value })} /></label>
        <p className="hint full">The new model is registered when training ends. Compare its mAP with the current model before switching to it.</p>
      </div>
      {err ? <ErrorBox error={err} /> : null}
    </Dialog>
  );
}

function LogDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const { data } = useLoad(() => get<string>(`/training-jobs/${id}/log`), [id], 4000);
  return <Dialog title={`Training job #${id}`} onClose={onClose} wide><pre className="log">{data || "Loading"}</pre></Dialog>;
}
