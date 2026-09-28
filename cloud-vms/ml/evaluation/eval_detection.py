"""Detection metrics (precision, recall, mAP) + latency for one or more models on a YOLO dataset.

    python ml/evaluation/eval_detection.py --data data/datasets/college_v1/data.yaml \
        --models data/weights/yolo26n.pt data/weights/college_v1.pt --split val --register

Object-detection metrics are reported separately from incident (event) metrics — see eval_events.py.
The report records dataset, split, image size, device and model so results are reproducible.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import dump, register_perf_run  # noqa: E402


def evaluate(model_path: str, data: str, split: str, imgsz: int, device: str) -> dict:
    from ultralytics import YOLO
    t0 = time.time()
    m = YOLO(model_path)
    r = m.val(data=data, split=split, imgsz=imgsz, device=device, verbose=False, plots=False)
    per_class = {}
    for i, c in enumerate(r.box.ap_class_index):
        per_class[r.names[int(c)]] = {"precision": round(float(r.box.p[i]), 4), "recall": round(float(r.box.r[i]), 4),
                                      "mAP50": round(float(r.box.ap50[i]), 4), "mAP50_95": round(float(r.box.ap[i]), 4)}
    return {"model": model_path, "precision": round(float(r.box.mp), 4), "recall": round(float(r.box.mr), 4),
            "mAP50": round(float(r.box.map50), 4), "mAP50_95": round(float(r.box.map), 4), "per_class": per_class,
            "speed_ms": {k: round(float(v), 2) for k, v in r.speed.items()}, "eval_seconds": round(time.time() - t0, 1)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--split", default="val")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--out", default="runs/eval_detection.json")
    ap.add_argument("--register", action="store_true", help="store results in the app (Performance page)")
    a = ap.parse_args()
    results = [evaluate(m, a.data, a.split, a.imgsz, a.device) for m in a.models]
    report = {"data": a.data, "split": a.split, "imgsz": a.imgsz, "device": a.device, "results": results}
    dump(a.out, report)
    for r in results:
        print(f"{Path(r['model']).name:32s} P={r['precision']:.3f} R={r['recall']:.3f} "
              f"mAP50={r['mAP50']:.3f} mAP50-95={r['mAP50_95']:.3f} infer={r['speed_ms'].get('inference', 0)} ms")
    if a.register:
        register_perf_run(f"Detection eval on {Path(a.data).parent.name} ({a.split})", "detection_eval",
                          {k: v for k, v in report.items() if k != "results"}, results)


if __name__ == "__main__":
    main()
