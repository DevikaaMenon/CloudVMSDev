"""Convert CrowdHuman (person detection in crowds) to YOLO format with canonical class ids.

Download from https://www.crowdhuman.org/ : CrowdHuman_train01-03.zip, CrowdHuman_val.zip,
annotation_train.odgt, annotation_val.odgt. Unzip the images so the folder looks like

    crowdhuman/
      Images/            <- all .jpg from the train and val zips
      annotation_train.odgt
      annotation_val.odgt

    python ml/datasets/prepare_crowdhuman.py --src D:/data/crowdhuman --out data/datasets/crowdhuman
    python ml/datasets/prepare_crowdhuman.py --src ... --out ... --box vbox --limit 3000   # quick subset

Uses the human-annotated full-body boxes ("fbox", default) or visible boxes ("vbox"); boxes tagged
"mask" (crowds / reflections) and boxes flagged ignore are skipped. Writes YOLO labels with class 0
(person) and a data.yaml using the project's canonical class list.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import CANONICAL_ID, dump, write_data_yaml  # noqa: E402


def convert(odgt: Path, images: Path, out: Path, split: str, box: str, limit: int | None, link: bool) -> dict:
    (out / "images" / split).mkdir(parents=True, exist_ok=True)
    (out / "labels" / split).mkdir(parents=True, exist_ok=True)
    n_img = n_box = skipped = 0
    with open(odgt) as f:
        for line in f:
            if limit and n_img >= limit:
                break
            rec = json.loads(line)
            src = images / f"{rec['ID']}.jpg"
            if not src.exists():
                skipped += 1
                continue
            img = cv2.imread(str(src))
            if img is None:
                skipped += 1
                continue
            h, w = img.shape[:2]
            rows = []
            for g in rec.get("gtboxes", []):
                if g.get("tag") != "person" or g.get("extra", {}).get("ignore", 0) == 1:
                    continue
                x, y, bw, bh = g[box]
                x1, y1, x2, y2 = max(0, x), max(0, y), min(w, x + bw), min(h, y + bh)
                if x2 - x1 < 4 or y2 - y1 < 4:
                    continue
                rows.append(f"{CANONICAL_ID['person']} {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} "
                            f"{(x2 - x1) / w:.6f} {(y2 - y1) / h:.6f}")
            dst = out / "images" / split / src.name
            if not dst.exists():
                if link:
                    try:
                        os.link(src, dst)
                    except OSError:
                        shutil.copyfile(src, dst)
                else:
                    shutil.copyfile(src, dst)
            (out / "labels" / split / f"{src.stem}.txt").write_text("\n".join(rows))
            n_img += 1
            n_box += len(rows)
    return {"images": n_img, "boxes": n_box, "skipped": skipped}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--box", default="fbox", choices=["fbox", "vbox"])
    ap.add_argument("--limit", type=int, help="max images per split (quick experiments)")
    ap.add_argument("--copy", action="store_true", help="copy images instead of hard-linking")
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    images = src / "Images" if (src / "Images").exists() else src
    stats = {}
    for split, name in (("train", "annotation_train.odgt"), ("val", "annotation_val.odgt")):
        if (src / name).exists():
            stats[split] = convert(src / name, images, out, split, a.box, a.limit, not a.copy)
            print(split, stats[split])
    if not stats:
        raise SystemExit("no annotation_*.odgt files found in --src")
    y = write_data_yaml(out, {"train": "images/train", "val": "images/val"})
    dump(out / "stats.json", {"source": "CrowdHuman", "box": a.box, **stats})
    print(f"wrote {y}")


if __name__ == "__main__":
    main()
