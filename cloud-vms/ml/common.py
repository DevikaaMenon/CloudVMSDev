"""Shared helpers for the ml/ scripts (paths, canonical classes, storing results in the app DB)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.analytics.classes import CANONICAL_CLASSES, normalize_label  # noqa: E402

# Fixed class order for every dataset this project writes, so models trained on
# different sources are interchangeable. id -> name
CANONICAL_ORDER = ["person", "bicycle", "two_wheeler", "three_wheeler", "car", "van", "lcv", "bus", "truck"]
CANONICAL_ID = {n: i for i, n in enumerate(CANONICAL_ORDER)}

# Common aliases seen in public datasets -> canonical class
ALIASES = {
    "pedestrian": "person", "people": "person", "human": "person", "rider": "person",
    "motorcycle": "two_wheeler", "motorbike": "two_wheeler", "bike": "two_wheeler", "scooter": "two_wheeler",
    "scooty": "two_wheeler", "twowheeler": "two_wheeler", "two_wheelers": "two_wheeler",
    "auto": "three_wheeler", "autorickshaw": "three_wheeler", "auto_rickshaw": "three_wheeler",
    "rickshaw": "three_wheeler", "tuk_tuk": "three_wheeler", "threewheeler": "three_wheeler",
    "cycle": "bicycle", "hatchback": "car", "sedan": "car", "suv": "car", "muv": "car", "jeep": "car",
    "taxi": "car", "minibus": "bus", "mini_bus": "bus", "school_bus": "bus",
    "tempo_traveller": "van", "tempo": "lcv", "pickup": "lcv", "mini_truck": "lcv",
    "lorry": "truck", "heavy_vehicle": "truck", "tractor": "truck",
}


def to_canonical(label: str, extra: dict | None = None) -> str | None:
    n = normalize_label(label)
    if extra and n in {normalize_label(k): v for k, v in extra.items()}:
        v = {normalize_label(k): v for k, v in extra.items()}[n]
        return normalize_label(v) if v else None
    if n in CANONICAL_CLASSES:
        return n
    return ALIASES.get(n)


def write_data_yaml(out_dir: Path, splits: dict[str, str]) -> Path:
    import yaml
    data = {"path": str(out_dir.resolve()), **splits,
            "names": {i: n for i, n in enumerate(CANONICAL_ORDER)}}
    p = out_dir / "data.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


def register_perf_run(name: str, kind: str, config: dict, results) -> int | None:
    """Store results so they appear on the app's System / Performance page."""
    try:
        from app.db import init_db, session_scope
        from app.models import PerfRun
        init_db()
        with session_scope() as db:
            run = PerfRun(name=name, kind=kind, config=config, results=results)
            db.add(run)
            db.flush()
            return run.id
    except Exception as exc:  # results are still written to disk
        print(f"(could not store results in the app database: {exc})")
        return None


def dump(path: str | Path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, default=str))
