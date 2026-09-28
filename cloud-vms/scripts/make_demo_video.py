"""Generate a synthetic 'campus gate' demo video (no real footage needed).

People and a bus are cut from the sample images that ship with Ultralytics
and animated over a drawn gate scene, so the real YOLO model detects them:
* 3 pedestrians walk in through the gate (top -> bottom), 1 walks out;
* one pedestrian cuts across into the right-hand "staff only" area;
* a bus drives across the road.

    python scripts/make_demo_video.py            -> data/uploads/demo_gate.mp4

This is only for demos and tests. Real evaluation must use real footage
(your college gate videos or the public datasets in ml/datasets).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def _assets() -> Path:
    import ultralytics
    return Path(ultralytics.__file__).parent / "assets"


def load_sprites():
    bus_img = cv2.imread(str(_assets() / "bus.jpg"))
    people = [bus_img[390:905, 40:245], bus_img[398:865, 215:350], bus_img[388:885, 660:812]]
    bus = bus_img[225:755, 0:810]
    return people, bus


def scene(w: int, h: int) -> np.ndarray:
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (70, 110, 80)  # grass
    cv2.rectangle(img, (0, int(h * 0.30)), (w, int(h * 0.48)), (80, 80, 80), -1)  # road
    for x in range(0, w, 80):
        cv2.line(img, (x, int(h * 0.39)), (x + 40, int(h * 0.39)), (220, 220, 220), 3)
    cv2.rectangle(img, (int(w * 0.30), int(h * 0.48)), (int(w * 0.62), h), (150, 150, 145), -1)  # footpath
    cv2.rectangle(img, (int(w * 0.70), int(h * 0.62)), (w, h), (120, 105, 90), -1)  # staff-only area
    for gx in (int(w * 0.29), int(w * 0.62)):  # gate posts
        cv2.rectangle(img, (gx - 12, int(h * 0.40)), (gx + 12, int(h * 0.56)), (40, 40, 160), -1)
    noise = np.random.default_rng(0).integers(0, 12, img.shape, dtype=np.uint8)
    return cv2.add(img, noise)


def paste(dst: np.ndarray, sprite: np.ndarray, cx: float, bottom: float, height: int) -> None:
    sh, sw = sprite.shape[:2]
    scale = height / sh
    sp = cv2.resize(sprite, (max(1, int(sw * scale)), max(1, height)))
    h, w = sp.shape[:2]
    x1, y1 = int(cx - w / 2), int(bottom - h)
    X1, Y1, X2, Y2 = max(0, x1), max(0, y1), min(dst.shape[1], x1 + w), min(dst.shape[0], y1 + h)
    if X2 <= X1 or Y2 <= Y1:
        return
    crop = sp[Y1 - y1:Y2 - y1, X1 - x1:X2 - x1].astype(np.float32)
    mask = np.ones(crop.shape[:2], np.float32)
    k = max(3, (min(crop.shape[:2]) // 12) | 1)
    mask = cv2.GaussianBlur(cv2.copyMakeBorder(mask[2:-2, 2:-2], 2, 2, 2, 2, cv2.BORDER_CONSTANT, 0)
                            if mask.shape[0] > 4 and mask.shape[1] > 4 else mask, (k, k), 0)[..., None]
    roi = dst[Y1:Y2, X1:X2].astype(np.float32)
    dst[Y1:Y2, X1:X2] = (roi * (1 - mask) + crop * mask).astype(np.uint8)


def lerp(a, b, t):
    return a + (b - a) * min(1.0, max(0.0, t))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "uploads" / "demo_gate.mp4"))
    ap.add_argument("--seconds", type=int, default=40)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    a = ap.parse_args()
    people, bus = load_sprites()
    w, h, fps = a.width, a.height, a.fps
    bg = scene(w, h)
    # different people must look different (colour-shifted copies), otherwise appearance re-ID
    # would rightly treat identical sprites as the same person
    # (sprite, start_s, dur_s, (x0, y0) -> (x1, y1) feet positions as fractions, height px)
    actors = [
        (people[0], 1, 9, (0.40, 0.25), (0.45, 1.05), 230),   # walks in
        (people[1], 5, 10, (0.52, 0.22), (0.50, 1.05), 220),  # walks in
        (np.ascontiguousarray(people[1][..., ::-1]), 12, 11, (0.44, 1.10), (0.42, 0.22), 230),  # walks out
        (people[0], 18, 12, (0.55, 0.25), (0.90, 0.98), 220),  # walks in and into the staff-only area
        (np.ascontiguousarray(people[0][..., [1, 0, 2]]), 27, 9, (0.35, 0.24), (0.38, 1.05), 220),  # walks in
    ]
    vehicles = [(bus, 3, 8, (-0.30, 0.47), (1.35, 0.47), 260), (bus, 22, 7, (1.35, 0.46), (-0.30, 0.46), 250)]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(a.seconds * fps):
        t = i / fps
        frame = bg.copy()
        drawn = []
        for sprite, t0, dur, p0, p1, ht in vehicles + actors:
            if t0 <= t <= t0 + dur:
                u = (t - t0) / dur
                x, y = lerp(p0[0], p1[0], u) * w, lerp(p0[1], p1[1], u) * h
                depth = 0.55 + 0.6 * (y / h)  # farther = smaller
                drawn.append((y, sprite, x, int(ht * depth)))
        for y, sprite, x, ht in sorted(drawn, key=lambda d: d[0]):  # painter's order
            paste(frame, sprite, x, y, ht)
        vw.write(frame)
    vw.release()
    print(f"wrote {out} ({a.seconds}s @ {fps} fps)")


if __name__ == "__main__":
    main()
