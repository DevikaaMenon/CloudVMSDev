"""Clip-level ground truth from a folder-per-class video dataset.

Kaggle "Burglary and Vandalism Detection Dataset" (CC0; classes burglary, vandalism, normal):
    kaggle datasets download -d kizitoryan/burglary-and-vandalism-detection-dataset -p D:/data/bv --unzip
    python ml/datasets/prepare_clip_dataset.py --root D:/data/bv --positive burglary vandalism \
        --negative normal --event-type INTRUSION_DETECTED --out data/eval/kaggle_bv.json

Clip-level labels only say *whether* a clip contains the incident, not *when*; eval_events.py scores
these as: positive clip with >= 1 alarm = TP, positive clip without alarm = FN, alarm in a negative
clip = FP.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import dump  # noqa: E402

VIDEO_EXT = {".mp4", ".avi", ".mkv", ".mov", ".mpg", ".mpeg", ".webm"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True)
    ap.add_argument("--positive", nargs="+", required=True, help="class folder names that contain incidents")
    ap.add_argument("--negative", nargs="+", default=["normal"])
    ap.add_argument("--event-type", default="INTRUSION_DETECTED")
    ap.add_argument("--clock", default="2026-01-05T23:00")
    ap.add_argument("--limit", type=int, help="max clips per class")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    pos = {p.lower() for p in a.positive}
    neg = {n.lower() for n in a.negative}
    per_class: dict[str, int] = {}
    videos = []
    for p in sorted(Path(a.root).rglob("*")):
        if p.suffix.lower() not in VIDEO_EXT:
            continue
        cls = next((part.lower() for part in reversed(p.parts[:-1]) if part.lower() in pos | neg), None)
        if cls is None:
            continue
        if a.limit and per_class.get(cls, 0) >= a.limit:
            continue
        per_class[cls] = per_class.get(cls, 0) + 1
        videos.append({"video": str(p), "category": cls, "clock": a.clock,
                       "label": "positive" if cls in pos else "negative", "event_type": a.event_type,
                       "events": []})
    dump(a.out, {"name": f"clip-level: {Path(a.root).name}", "default_zones": "full_frame_intrusion",
                 "videos": videos})
    print(per_class, "->", a.out)


if __name__ == "__main__":
    main()
