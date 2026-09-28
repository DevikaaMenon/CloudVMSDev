"""Download pretrained weights into data/weights.

    python ml/datasets/download_models.py --yolo26 n s     # COCO YOLO26 nano + small (Ultralytics, GitHub)
    python ml/datasets/download_models.py --uvh26          # IISc UVH-26 YOLOv11-S Indian-vehicle model (Hugging Face)
    python ml/datasets/download_models.py --uvh26 --size X # the larger YOLOv11-X variant

UVH-26 models: Apache-2.0, https://huggingface.co/iisc-aim/UVH-26 (Sharma et al., arXiv:2511.02563).
After downloading, open "Models & Data" in the app and switch the detector profile to
"specialized" (person model + UVH-26 vehicle model).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import ROOT  # noqa: E402

WEIGHTS = ROOT / "data" / "weights"


def download_yolo26(sizes: list[str]) -> None:
    from ultralytics import YOLO
    for s in sizes:
        target = WEIGHTS / f"yolo26{s}.pt"
        YOLO(str(target))  # Ultralytics downloads official weights to the given path
        print(f"ok  {target}")


def download_uvh26(size: str) -> Path:
    from huggingface_hub import HfApi, hf_hub_download
    repo = "iisc-aim/UVH-26"
    wanted = f"UVH-26-MV-YOLOv11-{size.upper()}.pt"
    files = HfApi().list_repo_files(repo)
    match = [f for f in files if f.endswith(wanted)]
    if not match:
        raise SystemExit(f"{wanted} not found in {repo}. Files available:\n  " + "\n  ".join(files))
    src = hf_hub_download(repo, match[0])
    dst = WEIGHTS / wanted
    shutil.copyfile(src, dst)
    from ultralytics import YOLO
    names = YOLO(str(dst)).names
    print(f"ok  {dst}\n    classes: {list(names.values())}")
    if size.upper() != "S":
        print("    note: config/models.yaml points at the -S file; register this one on the Models page "
              "or edit the yaml before first start.")
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yolo26", nargs="*", help="YOLO26 sizes to fetch: n s m l x")
    ap.add_argument("--uvh26", action="store_true", help="fetch the IISc UVH-26 vehicle model")
    ap.add_argument("--size", default="S", choices=["S", "X", "s", "x"], help="UVH-26 YOLOv11 size")
    a = ap.parse_args()
    WEIGHTS.mkdir(parents=True, exist_ok=True)
    if a.yolo26 is not None:
        download_yolo26(a.yolo26 or ["n"])
    if a.uvh26:
        download_uvh26(a.size)
    if a.yolo26 is None and not a.uvh26:
        ap.print_help()


if __name__ == "__main__":
    main()
