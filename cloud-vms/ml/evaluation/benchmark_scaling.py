"""Scalability benchmark: how does the pipeline behave as the number of cameras grows?

Simulates N live cameras (each replays a video in real time, like an RTSP feed) through the same
components the app uses — VideoSource -> InferenceService (shared, batched, bounded queues) ->
CameraAnalytics — and records, for each camera count:
    sustained input FPS, AI inference FPS, end-to-end latency (mean / p95), dropped frames,
    CPU %, RAM, GPU memory, and whether the system kept up.

    python ml/evaluation/benchmark_scaling.py --video data/uploads/demo_gate.mp4 --cameras 1 2 5 10 \
        --seconds 60 --fps 5 --register

Results are only valid for the hardware they were measured on; the report records it.
Don't claim the system supports N cameras without running this on the target machine.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import threading
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "inference"))
from common import dump, register_perf_run  # noqa: E402
from offline_run import load_zones, model_specs  # noqa: E402

from app.analytics.detector import Detector  # noqa: E402
from app.analytics.pipeline import AnalyticsConfig, CameraAnalytics  # noqa: E402
from app.workers.inference import InferenceJob, InferenceService  # noqa: E402
from app.workers.sources import VideoSource  # noqa: E402


class SimCamera:
    def __init__(self, cid: int, video: str, fps: float, service: InferenceService, zones):
        self.cid, self.fps, self.service = cid, fps, service
        self.src = VideoSource("file", video, realtime=True, loop=True)
        self.analytics = CameraAnalytics(cid, AnalyticsConfig(reid_enabled=True), zones)
        self.frames_in = self.inferred = self.dropped = 0
        self.lat: list[float] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.t = threading.Thread(target=self.loop, daemon=True)

    def on_result(self, job, dets, ms):
        with self._lock:  # analytics per camera must run in order
            self.analytics.process(job.ts, job.frame, dets)
            self.lat.append((time.perf_counter() - job.submitted) * 1000)
            self.inferred += 1

    def loop(self):
        if not self.src.open():
            return
        last = 0.0
        while not self._stop.is_set():
            ok, frame, ts = self.src.read()
            if not ok:
                continue
            self.frames_in += 1
            if ts - last >= 1.0 / self.fps - 0.01:
                last = ts
                if not self.service.submit(InferenceJob(self.cid, ts, frame, self.on_result)):
                    self.dropped += 1
        self.src.close()


def run_level(n: int, a, det, zones) -> dict:
    svc = InferenceService(det, batch_size=a.batch, max_pending_per_camera=2)
    cams = [SimCamera(i + 1, a.video, a.fps, svc, zones) for i in range(n)]
    proc = psutil.Process()
    proc.cpu_percent(None)
    for c in cams:
        c.t.start()
    time.sleep(a.warmup)
    base = [(c.frames_in, c.inferred, c.dropped, len(c.lat)) for c in cams]
    cpu_samples, mem_samples = [], []
    t0 = time.time()
    while time.time() - t0 < a.seconds:
        time.sleep(1)
        cpu_samples.append(proc.cpu_percent(None))
        mem_samples.append(proc.memory_info().rss / 1e6)
    dt = time.time() - t0
    stats = [(c.frames_in - b[0], c.inferred - b[1], c.dropped - b[2], c.lat[b[3]:]) for c, b in zip(cams, base)]
    for c in cams:
        c._stop.set()
    svc.stop()
    for c in cams:
        c.t.join(timeout=5)
    lat = sorted(x for s in stats for x in s[3])
    gpu = 0.0
    try:
        import torch
        if torch.cuda.is_available():
            gpu = torch.cuda.max_memory_allocated() / 1e6
    except Exception:
        pass
    target = a.fps * n
    achieved = sum(s[1] for s in stats) / dt
    return {
        "cameras": n,
        "input_fps_per_camera": round(sum(s[0] for s in stats) / dt / n, 2),
        "target_inference_fps_total": round(target, 2),
        "inference_fps_total": round(achieved, 2),
        "inference_fps_per_camera": round(achieved / n, 2),
        "keeps_up": achieved >= 0.9 * target,
        "latency_ms_mean": round(sum(lat) / len(lat), 1) if lat else None,
        "latency_ms_p95": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None,
        "dropped_frames_per_min": round(sum(s[2] for s in stats) / dt * 60, 1),
        "cpu_percent_mean": round(sum(cpu_samples) / len(cpu_samples), 1) if cpu_samples else None,
        "cpu_percent_of_machine": round(sum(cpu_samples) / len(cpu_samples) / psutil.cpu_count(), 1)
        if cpu_samples else None,
        "ram_mb_peak": round(max(mem_samples), 1) if mem_samples else None,
        "gpu_mem_mb_peak": round(gpu, 1),
        "batches": svc.batches, "measured_seconds": round(dt, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--zones", default=str(Path(__file__).resolve().parents[1] / "inference" / "example_zones.json"))
    ap.add_argument("--cameras", nargs="+", type=int, default=[1, 2, 5, 10])
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--warmup", type=float, default=5)
    ap.add_argument("--fps", type=float, default=5, help="inference FPS per camera")
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--profile", default="shared", choices=["shared", "specialized"])
    ap.add_argument("--weights", nargs="*")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--out", default="runs/benchmark_scaling.json")
    ap.add_argument("--register", action="store_true")
    a = ap.parse_args()
    det = Detector(model_specs(a.profile, a.weights), a.device, imgsz=a.imgsz).load()
    det.warmup()
    zones = load_zones(a.zones)
    hw = {"platform": platform.platform(), "cpu": platform.processor() or "", "cpu_count": psutil.cpu_count(),
          "ram_gb": round(psutil.virtual_memory().total / 1e9, 1), "device": det.device}
    try:
        import torch
        if torch.cuda.is_available():
            hw["gpu"] = torch.cuda.get_device_name(0)
    except Exception:
        pass
    results = []
    for n in a.cameras:
        print(f"-- {n} camera(s) ...", flush=True)
        r = run_level(n, a, det, zones)
        results.append(r)
        print(json.dumps(r))
    cfg = {"video": a.video, "inference_fps_per_camera": a.fps, "batch": a.batch, "model": det.version,
           "imgsz": a.imgsz, "seconds_per_level": a.seconds, "hardware": hw}
    dump(a.out, {"config": cfg, "results": results})
    print(f"\n{'cams':>4} {'ai fps':>8} {'target':>7} {'lat ms':>8} {'p95':>7} {'drop/min':>9} {'cpu%':>6}  ok")
    for r in results:
        print(f"{r['cameras']:>4} {r['inference_fps_total']:>8} {r['target_inference_fps_total']:>7} "
              f"{r['latency_ms_mean']!s:>8} {r['latency_ms_p95']!s:>7} {r['dropped_frames_per_min']:>9} "
              f"{r['cpu_percent_mean']!s:>6}  {'yes' if r['keeps_up'] else 'NO'}")
    if a.register:
        register_perf_run(f"Scaling benchmark ({det.device}, {a.fps} fps/camera)", "scaling", cfg, results)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)  # don't wait for native decoder threads during interpreter shutdown
