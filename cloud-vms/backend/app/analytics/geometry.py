"""2-D geometry used by zones and lines (pure functions, unit-tested)."""
from __future__ import annotations

import math
from typing import Sequence

Point = tuple[float, float]


def point_in_polygon(pt: Point, poly: Sequence[Point]) -> bool:
    """Ray casting. Points exactly on an edge count as inside."""
    x, y = pt
    n = len(poly)
    inside = False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if _on_segment(pt, (x1, y1), (x2, y2)):
            return True
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xin:
                inside = not inside
    return inside


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _on_segment(p: Point, a: Point, b: Point, eps: float = 1e-9) -> bool:
    if abs(_cross(a, b, p)) > eps * max(1.0, math.dist(a, b)):
        return False
    return (min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps and
            min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps)


def segments_intersect(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    d1, d2 = _cross(q1, q2, p1), _cross(q1, q2, p2)
    d3, d4 = _cross(p1, p2, q1), _cross(p1, p2, q2)
    if ((d1 > 0) != (d2 > 0)) and d1 != 0 and d2 != 0 and ((d3 > 0) != (d4 > 0)) and d3 != 0 and d4 != 0:
        return True
    return (_on_segment(p1, q1, q2) or _on_segment(p2, q1, q2) or
            _on_segment(q1, p1, p2) or _on_segment(q2, p1, p2))


def polygon_area(poly: Sequence[Point]) -> float:
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def is_simple_polygon(poly: Sequence[Point]) -> bool:
    """True if no two non-adjacent edges intersect."""
    n = len(poly)
    edges = [(poly[i], poly[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # adjacent edges share a vertex
            if segments_intersect(*edges[i], *edges[j]):
                return False
    return True


def validate_geometry(shape: str, points: list) -> list[str]:
    """Return a list of human readable problems (empty list = valid)."""
    problems: list[str] = []
    try:
        pts = [(float(p[0]), float(p[1])) for p in points]
    except (TypeError, ValueError, IndexError):
        return ["points must be a list of [x, y] pairs"]
    if any(not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0) for x, y in pts):
        problems.append("coordinates must be normalised to the range 0..1")
    if shape == "line":
        if len(pts) != 2:
            problems.append("a line needs exactly 2 points")
        elif math.dist(pts[0], pts[1]) < 0.01:
            problems.append("line is too short")
    elif shape == "polygon":
        if len(pts) < 3:
            problems.append("a polygon needs at least 3 points")
        elif len(pts) > 64:
            problems.append("a polygon may have at most 64 points")
        else:
            if polygon_area(pts) < 1e-4:
                problems.append("polygon area is too small")
            if not is_simple_polygon(pts):
                problems.append("polygon edges cross each other (self-intersection)")
    else:
        problems.append("shape must be 'polygon' or 'line'")
    return problems


def signed_side(pt: Point, a: Point, b: Point) -> float:
    """Signed perpendicular distance of pt from line a->b (positive = left side in image coords)."""
    length = math.dist(a, b) or 1.0
    return _cross(a, b, pt) / length


def to_pixels(points: Sequence[Sequence[float]], width: int, height: int) -> list[Point]:
    return [(float(x) * width, float(y) * height) for x, y in points]


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def intersection_over_first(a: Sequence[float], b: Sequence[float]) -> float:
    """Fraction of box a covered by box b."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area = max(1e-9, (a[2] - a[0]) * (a[3] - a[1]))
    return inter / area
