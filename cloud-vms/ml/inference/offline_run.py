"""Run the exact live analytics pipeline on a video file, offline, without the web app.

Used for evaluation (ml/evaluation/eval_events.py), benchmarking and quick
experiments on your own footage:

    python ml/inference/offline_run.py --video data/uploads/demo_gate.mp4 \
        --zones ml/inference/example_zones.json --fps 5 --out runs/demo.json --annotate runs/demo.mp4

Timestamps in the output are seconds from the start of the video.
Zones JSON: a list of {"id", "name", "zone_type", "points" (normalised), "severity",
"default_action", "config", "policies": [{"object_types", "schedule", "action"}]}.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import cv2  # noqa: E402
import yaml  # noqa: E402

from app.analytics.annotate import draw_tracks, draw_zones  # noqa: E402
from app.analytics.detector import Detector, ModelSpec  # noqa: E402
from app.analytics.pipeline import AnalyticsConfig, CameraAnalytics  # noqa: E402
from app.analytics.policy import PolicySpec  # noqa: E402
from app.analytics.rules import ZoneSpec  # noqa: E402


def load_zones(path: str | None) -> list[ZoneSpec]:
    if not path:
        return []
    data = json.loads(Path(path).read_text())
    zones = []
    for i, z in enumerate(data, start=1):
        zones.append(ZoneSpec(
            id=z.get("id", i), camera_id=0, name=z.get("name", f"zone{i}"), zone_type=z["zone_type"],
            shape="line" if z["zone_type"] == "line" else "polygon", points=z["points"],
            severity=z.get("severity", "medium"), default_action=z.get("default_action", "allow"),
            config=z.get("config", {}),
            policies=[PolicySpec(j, p.get("object_types", []), p.get("schedule", []), p.get("action", "alert"),
                                 p.get("severity"), True, p.get("name", "")) for j, p in enumerate(z.get("policies", []), 1)]))
    return zones


def model_specs(profile: str, weights: list[str] | None) -> list[ModelSpec]:
    """Build specs from config/models.yaml (by name) or explicit weights with a COCO-style map."""
    catalogue = {m["name"]: m for m in yaml.safe_load((ROOT / "config" / "models.yaml").read_text())["models"]}
    names = weights or (["coco-yolo26n"] if profile == "shared" else ["person-yolo26n", "vehicles-uvh26-yolo11s"])
    specs = []
    for n in names:
        m = catalogue.get(n)
        if m is None:  # a path to custom weights: identity map onto canonical names + COCO names
            m = {"name": Path(n).stem, "weights": n, "class_map": {**catalogue["coco-yolo26n"]["class_map"],
                 **{k: k for k in ("person", "bicycle", "two_wheeler", "three_wheeler", "car", "van", "lcv", "bus",
                                   "truck")}}}
        w = m["weights"]
        if not Path(w).is_absolute():
            w = str((ROOT / "data" / "weights" / w) if Path(w).parent == Path(".") else (ROOT / w))
        specs.append(ModelSpec(m["name"], w, m["class_map"], m.get("role", "shared")))
    return specs


def run(video: str, zones: list[ZoneSpec], fps: float, specs: list[ModelSpec], config: dict | None = None,
        clock: datetime | None = None, annotate: str | None = None, device: str = "auto", imgsz: int = 640,
        detector=None, max_seconds: float | None = None) -> dict:
    cfg = AnalyticsConfig.from_dict({**(config or {}), "inference_fps": fps})
    det = detector or Detector(specs, device, imgsz=imgsz, conf=cfg.detector_conf).load()
    start_clock = clock or datetime.now()
    cam = CameraAnalytics(0, cfg, zones, local_time=lambda ts: start_clock + timedelta(seconds=ts),
                          device=getattr(det, "device", "cpu"))
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, round(src_fps / fps))
    writer = None
    crossings, events, tracks, lat = [], [], [], []
    idx = 0
    t_start = time.perf_counter()
    while True:
        ok = cap.grab()
        if not ok:
            break
        ts = idx / src_fps
        if max_seconds and ts > max_seconds:
            break
        if idx % step == 0:
            _, frame = cap.retrieve()
            t0 = time.perf_counter()
            dets = det.predict([frame])[0]
            res = cam.process(ts, frame, dets)
            lat.append((time.perf_counter() - t0) * 1000)
            crossings += [{"ts": round(c.ts, 2), "direction": c.direction, "group": c.group, "class": c.cls,
                           "root": c.root_uid, "zone_id": c.zone_id} for c in res.crossings]
            for d in res.decisions:
                if d.kind != "drop":
                    c = d.candidate
                    events.append({"ts": round(c.ts, 2), "type": c.event_type, "kind": d.kind, "zone_id": c.zone_id,
                                   "class": c.cls, "root": c.root_uid, "title": c.title})
            tracks += [vars(t) for t in res.terminated]
            if annotate:
                img = frame.copy()
                draw_zones(img, zones, res.zone_occupancy, res.line_totals)
                draw_tracks(img, res.tracks)
                if writer is None:
                    Path(annotate).parent.mkdir(parents=True, exist_ok=True)
                    writer = cv2.VideoWriter(annotate, cv2.VideoWriter_fourcc(*"mp4v"), fps, (img.shape[1], img.shape[0]))
                writer.write(img)
        idx += 1
    tracks += [vars(t) for t in cam.flush()]
    cap.release()
    if writer:
        writer.release()
    wall = time.perf_counter() - t_start
    duration = idx / src_fps
    uniq = lambda g, d: len({c["root"] for c in crossings if c["group"] == g and c["direction"] == d})  # noqa: E731
    return {
        "video": str(video), "duration_s": round(duration, 1), "frames_analysed": len(lat),
        "model": getattr(det, "version", "?"), "device": getattr(det, "device", "?"), "inference_fps": fps,
        "processing": {"wall_s": round(wall, 1), "mean_ms_per_frame": round(sum(lat) / len(lat), 1) if lat else 0,
                       "p95_ms": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 1) if lat else 0,
                       "realtime_factor": round(duration / wall, 2) if wall else None},
        "counts": {"people_in": uniq("person", "in"), "people_out": uniq("person", "out"),
                   "vehicles_in": uniq("vehicle", "in"), "vehicles_out": uniq("vehicle", "out"),
                   "unique_people_seen": len({t["root_uid"] for t in tracks if t["group"] == "person" and t["hits"] >= 2}),
                   "unique_vehicles_seen": len({t["root_uid"] for t in tracks if t["group"] == "vehicle" and t["hits"] >= 2})},
        "crossings": crossings, "events": events,
        "tracks": [{k: t[k] for k in ("uid", "root_uid", "cls", "group", "first_ts", "last_ts", "hits")} for t in tracks],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--zones", help="zones JSON file")
    ap.add_argument("--fps", type=float, default=5.0, help="inference samples per second")
    ap.add_argument("--profile", default="shared", choices=["shared", "specialized"])
    ap.add_argument("--weights", nargs="*", help="model names from config/models.yaml or .pt paths")
    ap.add_argument("--config", help="JSON with AnalyticsConfig overrides (tracker, reid, events ...)")
    ap.add_argument("--clock", help="local wall-clock time of the first frame, e.g. 2026-09-27T22:00 (for schedules)")
    ap.add_argument("--annotate", help="write an annotated MP4 here")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--out", help="write JSON results here")
    a = ap.parse_args()
    res = run(a.video, load_zones(a.zones), a.fps, model_specs(a.profile, a.weights),
              json.loads(Path(a.config).read_text()) if a.config else None,
              datetime.fromisoformat(a.clock) if a.clock else None, a.annotate, a.device, a.imgsz,
              max_seconds=a.max_seconds)
    text = json.dumps(res, indent=2)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text)
    print(json.dumps({k: res[k] for k in ("video", "duration_s", "model", "device", "processing", "counts")}, indent=2))
    print(f"events: {len([e for e in res['events'] if e['kind'] == 'new'])}")


if __name__ == "__main__":
    main()
