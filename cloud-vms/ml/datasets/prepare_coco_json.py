"""Convert COCO-JSON detection datasets (e.g. IISc UVH-26) to YOLO format with canonical classes.

UVH-26 (Bengaluru CCTV, 14 Indian vehicle classes, CC-BY-4.0):
    pip install huggingface_hub
    python ml/datasets/prepare_coco_json.py --hf-dataset iisc-aim/UVH-26 --download-to D:/data/uvh26 \
           --out data/datasets/uvh26 --limit 4000

Any COCO dataset already on disk:
    python ml/datasets/prepare_coco_json.py --ann path/to/train.json --images path/to/images \
           --split train --out data/datasets/mydata

Category names are mapped automatically (Hatchback/Sedan/SUV/MUV -> car, Two-wheeler -> two_wheeler,
Three-wheeler -> three_wheeler, Tempo-traveller -> van, ...). Use --map extra.yaml to override;
map a name to null to drop it. Unmapped categories are dropped and reported.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import CANONICAL_ID, dump, to_canonical, write_data_yaml  # noqa: E402


def find_image(images_root: Path, file_name: str) -> Path | None:
    p = images_root / file_name
    if p.exists():
        return p
    p = images_root / Path(file_name).name
    if p.exists():
        return p
    hits = list(images_root.rglob(Path(file_name).name))
    return hits[0] if hits else None


def convert(ann: Path, images_root: Path, out: Path, split: str, extra_map: dict | None, limit: int | None) -> dict:
    data = json.loads(ann.read_text())
    cats = {c["id"]: c["name"] for c in data["categories"]}
    cmap = {cid: to_canonical(name, extra_map) for cid, name in cats.items()}
    dropped_names = sorted({cats[c] for c, v in cmap.items() if v is None})
    by_img = defaultdict(list)
    for a in data["annotations"]:
        if a.get("iscrowd"):
            continue
        by_img[a["image_id"]].append(a)
    (out / "images" / split).mkdir(parents=True, exist_ok=True)
    (out / "labels" / split).mkdir(parents=True, exist_ok=True)
    counts, n_img, missing = Counter(), 0, 0
    for img in data["images"]:
        if limit and n_img >= limit:
            break
        src = find_image(images_root, img["file_name"])
        if src is None:
            missing += 1
            continue
        w, h = img["width"], img["height"]
        rows = []
        for a in by_img.get(img["id"], []):
            cls = cmap.get(a["category_id"])
            if cls is None:
                continue
            x, y, bw, bh = a["bbox"]
            if bw < 2 or bh < 2:
                continue
            rows.append(f"{CANONICAL_ID[cls]} {(x + bw / 2) / w:.6f} {(y + bh / 2) / h:.6f} {bw / w:.6f} {bh / h:.6f}")
            counts[cls] += 1
        stem = f"{Path(img['file_name']).stem}_{img['id']}"
        dst = out / "images" / split / f"{stem}{src.suffix}"
        if not dst.exists():
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
        (out / "labels" / split / f"{stem}.txt").write_text("\n".join(rows))
        n_img += 1
    return {"images": n_img, "missing_images": missing, "boxes_per_class": dict(counts),
            "category_mapping": {cats[c]: v for c, v in cmap.items()}, "dropped_categories": dropped_names}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hf-dataset", help="Hugging Face dataset repo id to download (e.g. iisc-aim/UVH-26)")
    ap.add_argument("--download-to", help="where to put the downloaded dataset")
    ap.add_argument("--ann", nargs="*", help="COCO json file(s)")
    ap.add_argument("--images", help="root folder of the images")
    ap.add_argument("--split", nargs="*", help="split name for each --ann (default: guessed from file name)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--map", help="YAML {source_name: canonical_or_null}")
    ap.add_argument("--limit", type=int, help="max images per split")
    a = ap.parse_args()
    extra = yaml.safe_load(Path(a.map).read_text()) if a.map else None
    out = Path(a.out)
    if a.hf_dataset:
        from huggingface_hub import snapshot_download
        root = Path(snapshot_download(a.hf_dataset, repo_type="dataset", local_dir=a.download_to))
        anns = [p for p in root.rglob("*.json") if p.stat().st_size > 1000]
        images_root = root
    else:
        if not a.ann or not a.images:
            raise SystemExit("give --hf-dataset, or --ann and --images")
        anns, images_root = [Path(p) for p in a.ann], Path(a.images)
    stats, splits = {}, {}
    for i, ann in enumerate(anns):
        try:
            head = json.loads(ann.read_text())
            if not {"images", "annotations", "categories"} <= set(head):
                continue
        except (ValueError, UnicodeDecodeError):
            continue
        name = (a.split[i] if a.split and i < len(a.split) else
                "val" if "val" in ann.stem.lower() else "test" if "test" in ann.stem.lower() else "train")
        stats[f"{name}:{ann.name}"] = convert(ann, images_root, out, name, extra, a.limit)
        splits[name] = f"images/{name}"
        print(name, ann.name, {k: v for k, v in stats[f'{name}:{ann.name}'].items() if k != 'category_mapping'})
    if not stats:
        raise SystemExit("no COCO annotation files found")
    if "val" not in splits:
        splits["val"] = splits.get("test", "images/train")
    y = write_data_yaml(out, splits)
    dump(out / "stats.json", {"source": a.hf_dataset or "coco-json", **stats})
    print(f"wrote {y}")


if __name__ == "__main__":
    main()
