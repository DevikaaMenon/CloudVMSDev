"""Build intrusion ground truth from UCF-Crime (Sultani et al., CVPR 2018).

Only the intrusion-related categories are used (default: Burglary, Stealing, Vandalism) plus a
sample of Normal videos as negatives. The official temporal annotations for the test videos give
the anomaly intervals, which become INTRUSION_DETECTED ground-truth events.

    python ml/datasets/prepare_ucf_crime.py --root D:/data/UCF_Crime \
        --annotations D:/data/UCF_Crime/Temporal_Anomaly_Annotation_for_Testing_Videos.txt \
        --normal 20 --out data/eval/ucf_intrusion.json

Annotation lines look like:  Burglary005_x264.mp4  Burglary  4710  5040  -1  -1
(two optional intervals in frame numbers, -1 = none).

Zones: UCF-Crime clips come from many unrelated cameras, so by default each video is evaluated
with a full-frame intrusion zone ("any person moving in view during protected hours"). For a
stricter evaluation, draw real zones for a subset and add "zones": "path.json" per video.
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import dump  # noqa: E402

VIDEO_EXT = {".mp4", ".avi", ".mkv", ".mpg", ".mpeg"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="folder containing the UCF-Crime videos (searched recursively)")
    ap.add_argument("--annotations", required=True)
    ap.add_argument("--classes", nargs="*", default=["Burglary", "Stealing", "Vandalism"])
    ap.add_argument("--normal", type=int, default=20, help="how many Normal test videos to add as negatives")
    ap.add_argument("--clock", default="2026-01-05T23:00", help="pretend local time (night -> perimeter armed)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    index = {p.name: p for p in Path(a.root).rglob("*") if p.suffix.lower() in VIDEO_EXT}
    videos, normals, missing = [], [], 0
    for line in Path(a.annotations).read_text().splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        name, cat = parts[0], parts[1]
        path = index.get(name)
        if path is None:
            missing += 1
            continue
        if cat == "Normal":
            normals.append({"video": str(path), "label": "negative", "category": "Normal", "events": [],
                            "clock": a.clock})
            continue
        if cat not in a.classes:
            continue
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.release()
        f = [int(x) for x in parts[2:6]]
        events = [{"type": "INTRUSION_DETECTED", "start": round(s / fps, 2), "end": round(e / fps, 2)}
                  for s, e in ((f[0], f[1]), (f[2], f[3])) if s >= 0 and e > s]
        videos.append({"video": str(path), "label": "positive", "category": cat, "events": events, "clock": a.clock})
    random.Random(a.seed).shuffle(normals)
    videos += normals[: a.normal]
    gt = {"name": "UCF-Crime intrusion subset", "source": "https://www.crcv.ucf.edu/projects/real-world/",
          "default_zones": "full_frame_intrusion", "videos": videos}
    dump(a.out, gt)
    print(f"{len(videos)} videos ({sum(v['label'] == 'positive' for v in videos)} positive), "
          f"{missing} annotated videos not found under --root -> {a.out}")


if __name__ == "__main__":
    main()
