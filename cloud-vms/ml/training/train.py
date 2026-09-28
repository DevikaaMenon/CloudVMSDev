"""Fine-tune a YOLO detector (reproducible, checkpointed) — runs locally, on Kaggle, Colab or EC2.

    python ml/training/train.py --data data/datasets/college_v1/data.yaml --model data/weights/yolo26n.pt \
        --epochs 50 --imgsz 640 --batch 16 --project runs --name college_v1

On Kaggle / Colab: upload this repo's ml/ folder (or pip install ultralytics and copy this file),
attach the dataset, run the same command, then download ONLY runs/<name>/weights/best.pt and register
it in the app ("Models & Data" -> Upload weights). The full dataset never needs to be on your laptop.

Writes --result-json with the best weights path and validation metrics so the app can register the
model automatically.
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path


def pick_device(d: str) -> str:
    if d != "auto":
        return d
    try:
        import torch
        if torch.cuda.is_available():
            return "0"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--model", default="yolo26n.pt")
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--patience", type=int, default=15, help="early stopping patience (epochs)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--project", default="runs")
    ap.add_argument("--name", default="train")
    ap.add_argument("--resume", action="store_true", help="resume from the last checkpoint of this run")
    ap.add_argument("--result-json")
    a = ap.parse_args()

    from ultralytics import YOLO
    device = pick_device(a.device)
    t0 = time.time()
    if a.resume:
        last = Path(a.project) / a.name / "weights" / "last.pt"
        model = YOLO(str(last))
        model.train(resume=True)
    else:
        model = YOLO(a.model)
        model.train(data=a.data, epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, device=device,
                    patience=a.patience, seed=a.seed, deterministic=True, workers=a.workers,
                    project=a.project, name=a.name, exist_ok=True, plots=True, save_period=10)
    save_dir = Path(model.trainer.save_dir)
    best = save_dir / "weights" / "best.pt"
    metrics = YOLO(str(best)).val(data=a.data, imgsz=a.imgsz, device=device, split="val", verbose=False)
    names = metrics.names
    per_class = {}
    try:
        for i, c in enumerate(metrics.box.ap_class_index):
            per_class[names[int(c)]] = {"precision": round(float(metrics.box.p[i]), 4),
                                        "recall": round(float(metrics.box.r[i]), 4),
                                        "mAP50": round(float(metrics.box.ap50[i]), 4),
                                        "mAP50_95": round(float(metrics.box.ap[i]), 4)}
    except Exception:
        pass
    result = {
        "best": str(best.resolve()), "run_dir": str(save_dir.resolve()),
        "metrics": {"precision": round(float(metrics.box.mp), 4), "recall": round(float(metrics.box.mr), 4),
                    "mAP50": round(float(metrics.box.map50), 4), "mAP50_95": round(float(metrics.box.map), 4),
                    "per_class": per_class, "inference_ms_per_image": round(float(metrics.speed.get("inference", 0)), 2)},
        "config": {"data": a.data, "base_model": a.model, "epochs": a.epochs, "imgsz": a.imgsz, "batch": a.batch,
                   "device": device, "seed": a.seed, "patience": a.patience},
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "train_minutes": round((time.time() - t0) / 60, 1),
    }
    print(json.dumps(result["metrics"], indent=2))
    if a.result_json:
        Path(a.result_json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.result_json).write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
