"""Dataset import and fine-tuning jobs, run as background subprocesses of the ml/ tools.

Training on a laptop CPU is slow; for real training use the same script on
Kaggle / Colab / a GPU EC2 instance and register the resulting weights with
``POST /api/models``. The in-app runner is for small college datasets.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path

import yaml

from ..analytics.classes import CANONICAL_CLASSES, normalize_label
from ..core.config import PROJECT_ROOT, get_settings
from ..core.timeutil import utcnow
from ..db import session_scope
from ..models import Dataset, ModelVersion, TrainingJob

log = logging.getLogger("vms.training")
ML = PROJECT_ROOT / "ml"


def _run(cmd: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as f:
        f.write(("$ " + " ".join(cmd) + "\n").encode())
        f.flush()
        return subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=str(PROJECT_ROOT))


def import_dataset_async(dataset_id: int, raw_dir: Path) -> None:
    threading.Thread(target=_import, args=(dataset_id, raw_dir), daemon=True).start()


def _import(dataset_id: int, raw_dir: Path) -> None:
    with session_scope() as db:
        ds = db.get(Dataset, dataset_id)
        out = Path(ds.path)
    log_path = out / "import.log"
    rc = _run([sys.executable, str(ML / "college" / "import_college.py"), "labelled", "--src", str(raw_dir),
               "--out", str(out)], log_path)
    stats_file = out / "stats.json"
    with session_scope() as db:
        ds = db.get(Dataset, dataset_id)
        if rc == 0 and stats_file.exists():
            ds.status, ds.stats = "ready", json.loads(stats_file.read_text())
        else:
            ds.status = "failed"
            ds.stats = {"error": log_path.read_text(errors="ignore")[-1500:] if log_path.exists() else "import failed"}


def start_training(job_id: int) -> None:
    threading.Thread(target=_train, args=(job_id,), daemon=True).start()


def _train(job_id: int) -> None:
    s = get_settings()
    with session_scope() as db:
        job = db.get(TrainingJob, job_id)
        ds = db.get(Dataset, job.dataset_id)
        data_yaml = Path(ds.path) / "data.yaml" if Path(ds.path).is_dir() else Path(ds.path)
        run_dir = s.data_dir / "runs" / f"job{job_id}"
        job.status, job.log_path = "running", str(run_dir / "train.log")
        params, base, role, ds_name = dict(job.params), job.base_model, job.role, ds.name
    base_path = base if Path(base).exists() else str(s.weights_dir / Path(base).name)
    result_json = run_dir / "result.json"
    cmd = [sys.executable, str(ML / "training" / "train.py"), "--data", str(data_yaml), "--model", base_path,
           "--epochs", str(params.get("epochs", 30)), "--imgsz", str(params.get("imgsz", 640)),
           "--batch", str(params.get("batch", 8)), "--device", str(params.get("device", "auto")),
           "--project", str(run_dir), "--name", "train", "--result-json", str(result_json)]
    rc = _run(cmd, run_dir / "train.log")
    with session_scope() as db:
        job = db.get(TrainingJob, job_id)
        job.finished_at = utcnow()
        if rc != 0 or not result_json.exists():
            job.status = "failed"
            return
        result = json.loads(result_json.read_text())
        job.metrics = result.get("metrics", {})
        names = yaml.safe_load(data_yaml.read_text()).get("names", {})
        labels = names.values() if isinstance(names, dict) else names
        class_map = {str(n): normalize_label(n) for n in labels if normalize_label(n) in CANONICAL_CLASSES}
        mv = ModelVersion(name=f"{ds_name}-{role}-job{job_id}"[:120], role=role, weights=result["best"],
                          class_map=class_map, source="fine-tuned", dataset=ds_name, metrics=job.metrics,
                          notes=f"Fine-tuned from {base} for {params.get('epochs')} epochs", is_active=False)
        db.add(mv)
        db.flush()
        job.result_model_id, job.status = mv.id, "completed"
        log.info("training job %s finished -> model %s", job_id, mv.name)
