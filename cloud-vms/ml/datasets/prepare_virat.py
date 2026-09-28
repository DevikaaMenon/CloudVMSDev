"""Restricted-area access ground truth from the VIRAT Video Dataset (ground cameras).

VIRAT (https://viratdata.org, data use agreement required) has fixed outdoor cameras around
buildings and parking lots; the public DIVA annotations
(https://gitlab.kitware.com/viratdata/viratannotations) mark activities such as people entering /
exiting a facility and vehicles moving in lots. We turn chosen activities into
RESTRICTED_AREA_ACCESS ground truth; you draw the restricted zone (e.g. the building doorway or a
no-parking area) once per *scene* in the app or by hand.

    python ml/datasets/prepare_virat.py --videos D:/data/virat/videos_original \
        --annotations D:/data/viratannotations --activities Entering vehicle_turning_left \
        --zones-dir ml/datasets/virat_zones --out data/eval/virat_restricted.json

Annotation files are KPF YAML, one record per line, e.g.
  - { act: { act2: {Entering: 1.0}, id2: 7, timespan: [{tsr0: [3292, 3456]}], ... } }
Frames are converted to seconds with each video's real frame rate. Please check a few records of
your download against the parser output (it prints a summary) — annotation releases differ.

Zones: put one zones JSON per scene in --zones-dir named after the scene id, e.g.
VIRAT_S_0000.json for all VIRAT_S_0000xx_* videos (same format as ml/inference/example_zones.json).
Videos whose scene has no zones file are skipped.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import cv2
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import dump  # noqa: E402


def parse_activities(path: Path) -> list[tuple[str, int, int]]:
    out = []
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line.startswith("- {") or "act" not in line:
            continue
        try:
            rec = yaml.safe_load(line)[0]
        except Exception:
            continue
        act = rec.get("act") or {}
        names = act.get("act2") or act.get("act3") or {}
        spans = act.get("timespan") or []
        for sp in spans:
            fr = sp.get("tsr0")
            if not fr:
                continue
            for name in names:
                out.append((str(name), int(fr[0]), int(fr[1])))
    return out


def scene_of(video_stem: str) -> str:
    m = re.match(r"(VIRAT_S_\d{4})", video_stem)
    return m.group(1) if m else video_stem


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos", required=True)
    ap.add_argument("--annotations", required=True)
    ap.add_argument("--activities", nargs="+", default=["Entering"])
    ap.add_argument("--zones-dir", required=True)
    ap.add_argument("--clock", default="2026-01-05T22:00")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    wanted = {x.lower() for x in a.activities}
    vids = {p.stem: p for p in Path(a.videos).rglob("*.mp4")}
    ann_files = list(Path(a.annotations).rglob("*.activities.yml"))
    seen_names, videos, no_zone = Counter(), [], set()
    for f in ann_files:
        stem = f.name.replace(".activities.yml", "")
        if stem not in vids:
            continue
        acts = parse_activities(f)
        seen_names.update(n for n, _, _ in acts)
        zones = Path(a.zones_dir) / f"{scene_of(stem)}.json"
        if not zones.exists():
            no_zone.add(scene_of(stem))
            continue
        cap = cv2.VideoCapture(str(vids[stem]))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.release()
        events = [{"type": "RESTRICTED_AREA_ACCESS", "start": round(s / fps, 2), "end": round(e / fps, 2),
                   "activity": n} for n, s, e in acts if n.lower() in wanted]
        videos.append({"video": str(vids[stem]), "zones": str(zones), "clock": a.clock,
                       "label": "positive" if events else "negative", "events": events})
    dump(a.out, {"name": "VIRAT restricted-area access", "source": "https://viratdata.org", "videos": videos})
    print("activity names found:", dict(seen_names.most_common(25)))
    print(f"{len(videos)} videos written to {a.out}; scenes without a zones file: {sorted(no_zone)}")


if __name__ == "__main__":
    main()
