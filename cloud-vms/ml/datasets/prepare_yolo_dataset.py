"""Remap an existing YOLO-format dataset (e.g. from Kaggle or Roboflow) to the canonical classes.

Kaggle "Traffic Vehicles Object Detection" (Indian roads; classes Car, Number Plate, Blur Number Plate,
Two Wheeler, Auto, Bus, Truck):
    kaggle datasets download -d saumyapatel/traffic-vehicles-object-detection -p D:/data/traffic --unzip
    python ml/datasets/prepare_yolo_dataset.py --src D:/data/traffic --out data/datasets/kaggle_traffic

Class names are read from data.yaml / classes.txt / obj.names in --src. Number plates and any other
non-canonical labels are dropped. Splits: existing train/valid/test folders are kept; if the source
has no split, 80/20 is made (grouped by filename prefix so frames of one video don't leak).
"""
from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import CANONICAL_ID, dump, to_canonical, write_data_yaml  # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def read_names(src: Path) -> list[str]:
    for name in ("data.yaml", "dataset.yaml"):
        for p in list(src.glob(name)) + list(src.glob(f"*/{name}")):
            d = yaml.safe_load(p.read_text()) or {}
            n = d.get("names")
            if isinstance(n, dict):
                return [n[k] for k in sorted(n, key=int)]
            if isinstance(n, list):
                return n
    for name in ("classes.txt", "obj.names", "_darknet.labels"):
        hits = list(src.rglob(name))
        if hits:
            return [ln.strip() for ln in hits[0].read_text().splitlines() if ln.strip()]
    raise SystemExit("could not find class names (data.yaml, classes.txt or obj.names)")


def label_for(img: Path) -> Path | None:
    parts = list(img.parts)
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "images":
            p = Path(*parts[:i], "labels", *parts[i + 1:]).with_suffix(".txt")
            if p.exists():
                return p
    p = img.with_suffix(".txt")
    return p if p.exists() else None


def split_of(img: Path) -> str | None:
    s = {p.lower() for p in img.parts}
    if "train" in s:
        return "train"
    if "valid" in s or "val" in s:
        return "val"
    if "test" in s:
        return "test"
    return None


def group_key(stem: str) -> str:
    """Frames extracted from one video usually share a prefix (video_000123 / video_frame_12)."""
    return re.sub(r"([_\-. ]?(frame|f|img)?[_\-. ]?\d+)$", "", stem, flags=re.I) or stem


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--map", help="YAML {source_name: canonical_or_null} overrides")
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    extra = yaml.safe_load(Path(a.map).read_text()) if a.map else None
    names = read_names(src)
    remap = {i: to_canonical(n, extra) for i, n in enumerate(names)}
    print("class mapping:", {names[i]: v for i, v in remap.items()})
    images = [p for p in src.rglob("*") if p.suffix.lower() in IMG_EXT]
    explicit = {p: split_of(p) for p in images}
    if not any(explicit.values()):
        groups = defaultdict(list)
        for p in images:
            groups[group_key(p.stem)].append(p)
        keys = sorted(groups)
        random.Random(a.seed).shuffle(keys)
        n_val = max(1, int(len(keys) * a.val_frac))
        for i, k in enumerate(keys):
            for p in groups[k]:
                explicit[p] = "val" if i < n_val else "train"
    counts, per_split = Counter(), Counter()
    for img, split in explicit.items():
        split = split or "train"
        lab = label_for(img)
        rows = []
        if lab:
            for ln in lab.read_text().splitlines():
                parts = ln.split()
                if len(parts) < 5:
                    continue
                cls = remap.get(int(float(parts[0])))
                if cls is None:
                    continue
                rows.append(" ".join([str(CANONICAL_ID[cls])] + parts[1:5]))
                counts[cls] += 1
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        dst = out / "images" / split / img.name
        if not dst.exists():
            try:
                os.link(img, dst)
            except OSError:
                shutil.copyfile(img, dst)
        (out / "labels" / split / f"{img.stem}.txt").write_text("\n".join(rows))
        per_split[split] += 1
    splits = {s: f"images/{s}" for s in per_split}
    splits.setdefault("val", splits.get("test", "images/train"))
    y = write_data_yaml(out, splits)
    dump(out / "stats.json", {"source": str(src), "images_per_split": dict(per_split), "boxes_per_class": dict(counts),
                              "class_mapping": {names[i]: v for i, v in remap.items()}})
    print(dict(per_split), dict(counts))
    print(f"wrote {y}")


if __name__ == "__main__":
    main()
