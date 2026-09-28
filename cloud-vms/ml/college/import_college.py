"""Bring your own (college) footage into the project.

Step 1 - extract frames to label (skips near-identical frames, keeps the source video in the name):
    python ml/college/import_college.py frames --videos D:/college/gate_videos --out data/datasets/college_raw --fps 1

Step 2 (optional) - pre-label with the current model so you only correct boxes instead of drawing all:
    python ml/college/import_college.py autolabel --images data/datasets/college_raw --weights data/weights/yolo26n.pt

    Then open the folder in a labelling tool (CVAT, Label Studio or Roboflow), fix the boxes using the
    class names  person, bicycle, two_wheeler, three_wheeler, car, van, lcv, bus, truck  and export in
    "YOLO" format. Zip the export.

Step 3 - validate + split + write data.yaml (the web app runs this when you upload the zip on the
"Models & Data" page):
    python ml/college/import_college.py labelled --src path/to/yolo_export --out data/datasets/college_v1

Step 4 - ground truth for incident evaluation (fill in start/end seconds of real incidents):
    python ml/college/import_college.py events-template --videos D:/college/gate_videos --out data/eval/college_events.json

Splits are made by *source video*, never by frame, so near-identical frames of one clip can't appear in
both training and validation (that would inflate accuracy).
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

import cv2
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import CANONICAL_ID, CANONICAL_ORDER, dump, to_canonical, write_data_yaml  # noqa: E402

VIDEO_EXT = {".mp4", ".avi", ".mkv", ".mov", ".m4v", ".ts", ".mpg", ".mpeg", ".dav", ".h264", ".webm"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SEP = "__f"  # frame file names: <video_stem>__f000123.jpg


# ------------------------------------------------------------------ frames
def cmd_frames(a) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    vids = [p for p in Path(a.videos).rglob("*") if p.suffix.lower() in VIDEO_EXT] if Path(a.videos).is_dir() \
        else [Path(a.videos)]
    total = 0
    for v in sorted(vids):
        cap = cv2.VideoCapture(str(v))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        step = max(1, round(fps / a.fps))
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", v.stem)
        prev, i, kept = None, 0, 0
        while True:
            ok = cap.grab()
            if not ok:
                break
            if i % step == 0:
                _, frame = cap.retrieve()
                small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 36)).astype(np.float32)
                if prev is None or float(np.mean(np.abs(small - prev))) >= a.min_change:
                    cv2.imwrite(str(out / f"{stem}{SEP}{i:06d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
                    prev, kept = small, kept + 1
                    if a.max_per_video and kept >= a.max_per_video:
                        break
            i += 1
        cap.release()
        total += kept
        print(f"{v.name}: {kept} frames")
    (out / "classes.txt").write_text("\n".join(CANONICAL_ORDER) + "\n")
    print(f"{total} frames in {out} (class list written to classes.txt for your labelling tool)")


# ------------------------------------------------------------------ autolabel
def cmd_autolabel(a) -> None:
    from ultralytics import YOLO
    imgs = sorted(p for p in Path(a.images).iterdir() if p.suffix.lower() in IMG_EXT)
    model = YOLO(a.weights)
    names = model.names
    keep = {i: to_canonical(n) for i, n in names.items() if to_canonical(n)}
    n = 0
    for i in range(0, len(imgs), 16):
        batch = imgs[i:i + 16]
        for img, r in zip(batch, model.predict([str(p) for p in batch], conf=a.conf, verbose=False,
                                               classes=list(keep))):
            rows = []
            for (cx, cy, w, h), c in zip(r.boxes.xywhn.tolist(), r.boxes.cls.tolist()):
                rows.append(f"{CANONICAL_ID[keep[int(c)]]} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
            img.with_suffix(".txt").write_text("\n".join(rows))
            n += 1
    (Path(a.images) / "classes.txt").write_text("\n".join(CANONICAL_ORDER) + "\n")
    print(f"pre-labelled {n} images. These are MODEL GUESSES — review every image before training.")


# ------------------------------------------------------------------ labelled
def _find_names(src: Path) -> list[str] | None:
    for pat in ("data.yaml", "*/data.yaml", "dataset.yaml"):
        for p in src.glob(pat):
            d = yaml.safe_load(p.read_text()) or {}
            n = d.get("names")
            if isinstance(n, dict):
                return [n[k] for k in sorted(n, key=lambda x: int(x))]
            if isinstance(n, list):
                return n
    for name in ("classes.txt", "obj.names", "notes.json"):
        hits = list(src.rglob(name))
        if hits and name != "notes.json":
            return [ln.strip() for ln in hits[0].read_text().splitlines() if ln.strip()]
    return None


def _label_path(img: Path) -> Path | None:
    parts = list(img.parts)
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "images":
            p = Path(*parts[:i], "labels", *parts[i + 1:]).with_suffix(".txt")
            if p.exists():
                return p
    p = img.with_suffix(".txt")
    return p if p.exists() else None


def _video_of(stem: str) -> str:
    if SEP in stem:
        return stem.split(SEP)[0]
    return re.sub(r"([_\-. ]?(frame|f|img)?[_\-. ]?\d+)$", "", stem, flags=re.I) or stem


def cmd_labelled(a) -> None:
    src, out = Path(a.src), Path(a.out)
    names = _find_names(src)
    if names is None:
        raise SystemExit("No class list found (data.yaml / classes.txt / obj.names). Export in YOLO format.")
    extra = yaml.safe_load(Path(a.map).read_text()) if a.map else None
    remap = {i: to_canonical(n, extra) for i, n in enumerate(names)}
    unknown = [names[i] for i, v in remap.items() if v is None]
    print("class mapping:", {names[i]: v for i, v in remap.items()})
    if unknown:
        print(f"WARNING: these labels are not canonical and will be dropped: {unknown} (use --map to map them)")
    imgs = [p for p in src.rglob("*") if p.suffix.lower() in IMG_EXT]
    if not imgs:
        raise SystemExit("no images found")
    by_video = defaultdict(list)
    for p in imgs:
        by_video[_video_of(p.stem)].append(p)
    vids = sorted(by_video)
    random.Random(a.seed).shuffle(vids)
    n = len(vids)
    n_val = max(1, round(n * a.val)) if n > 1 else 0
    n_test = round(n * a.test) if n > 2 else 0
    split_of = {}
    for i, v in enumerate(vids):
        split_of[v] = "val" if i < n_val else "test" if i < n_val + n_test else "train"
    if n == 1:
        print("WARNING: only one source video; frames are split by time instead (first 80% train, last 20% val). "
              "Add more videos for a trustworthy evaluation.")
    counts = Counter()
    per_split = Counter()
    empty = bad = 0
    for v, files in by_video.items():
        files.sort()
        for k, img in enumerate(files):
            split = split_of[v] if n > 1 else ("val" if k >= int(len(files) * 0.8) else "train")
            lab = _label_path(img)
            rows = []
            if lab:
                for ln in lab.read_text().splitlines():
                    p = ln.split()
                    if len(p) < 5:
                        continue
                    try:
                        cls = remap.get(int(float(p[0])))
                        box = [float(x) for x in p[1:5]]
                    except ValueError:
                        bad += 1
                        continue
                    if cls is None or not all(0 <= x <= 1 for x in box) or box[2] <= 0 or box[3] <= 0:
                        bad += cls is not None
                        continue
                    rows.append(f"{CANONICAL_ID[cls]} " + " ".join(f"{x:.6f}" for x in box))
                    counts[cls] += 1
            if not rows:
                empty += 1
            (out / "images" / split).mkdir(parents=True, exist_ok=True)
            (out / "labels" / split).mkdir(parents=True, exist_ok=True)
            dst = out / "images" / split / f"{v}{SEP}{k:05d}{img.suffix.lower()}" if SEP not in img.stem \
                else out / "images" / split / img.name
            if not dst.exists():
                try:
                    os.link(img, dst)
                except OSError:
                    shutil.copyfile(img, dst)
            (out / "labels" / split / f"{dst.stem}.txt").write_text("\n".join(rows))
            per_split[split] += 1
    splits = {"train": "images/train", "val": "images/val"}
    if per_split.get("test"):
        splits["test"] = "images/test"
    y = write_data_yaml(out, splits)
    stats = {"source": str(src), "videos": n, "images": sum(per_split.values()), "images_per_split": dict(per_split),
             "boxes_per_class": dict(counts), "background_images": empty, "invalid_boxes": bad,
             "split_by": "source video" if n > 1 else "time (single video)", "data_yaml": str(y),
             "class_mapping": {names[i]: v for i, v in remap.items()}}
    dump(out / "stats.json", stats)
    print(stats)
    if sum(counts.values()) == 0:
        raise SystemExit("no usable boxes found — check the export format and class names")


# ------------------------------------------------------------------ events template
def cmd_events_template(a) -> None:
    vids = [p for p in Path(a.videos).rglob("*") if p.suffix.lower() in VIDEO_EXT] if Path(a.videos).is_dir() \
        else [Path(a.videos)]
    videos = []
    for v in sorted(vids):
        cap = cv2.VideoCapture(str(v))
        dur = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 25)
        cap.release()
        videos.append({"video": str(v), "duration_s": round(dur, 1), "zones": "PATH/TO/zones.json",
                       "clock": "2026-09-27T08:30", "label": "positive",
                       "events": [{"type": "RESTRICTED_AREA_ACCESS", "start": 0.0, "end": 0.0,
                                   "note": "replace with real incidents; delete if none"}],
                       "counts": {"people_in": None, "people_out": None, "vehicles_in": None, "vehicles_out": None}})
    dump(a.out, {"name": "college gate ground truth", "videos": videos})
    print(f"template for {len(videos)} videos -> {a.out}. Fill in real start/end seconds and manual counts.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("frames")
    f.add_argument("--videos", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--fps", type=float, default=1.0)
    f.add_argument("--min-change", type=float, default=4.0, help="skip frames whose mean pixel change is below this")
    f.add_argument("--max-per-video", type=int, default=0)
    al = sub.add_parser("autolabel")
    al.add_argument("--images", required=True)
    al.add_argument("--weights", default="data/weights/yolo26n.pt")
    al.add_argument("--conf", type=float, default=0.35)
    lb = sub.add_parser("labelled")
    lb.add_argument("--src", required=True)
    lb.add_argument("--out", required=True)
    lb.add_argument("--map", help="YAML {your_label: canonical_or_null}")
    lb.add_argument("--val", type=float, default=0.2)
    lb.add_argument("--test", type=float, default=0.1)
    lb.add_argument("--seed", type=int, default=0)
    et = sub.add_parser("events-template")
    et.add_argument("--videos", required=True)
    et.add_argument("--out", required=True)
    a = ap.parse_args()
    {"frames": cmd_frames, "autolabel": cmd_autolabel, "labelled": cmd_labelled,
     "events-template": cmd_events_template}[a.cmd](a)


if __name__ == "__main__":
    main()
