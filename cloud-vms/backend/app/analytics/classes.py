"""Canonical object taxonomy shared by every detector, rule and statistic.

Detectors trained on different datasets use different label names (COCO says
"motorcycle", UVH-26 says "Two-wheeler", a college model may say "bike").
Each model has a ``class_map`` that translates its labels into the canonical
classes below; the original label is preserved as ``subtype``.
"""
from __future__ import annotations

import re

PERSON = "person"
VEHICLE = "vehicle"

CANONICAL_CLASSES: dict[str, str] = {
    "person": PERSON,
    "bicycle": VEHICLE,
    "two_wheeler": VEHICLE,
    "three_wheeler": VEHICLE,
    "car": VEHICLE,
    "van": VEHICLE,
    "lcv": VEHICLE,
    "bus": VEHICLE,
    "truck": VEHICLE,
}

GROUPS = (PERSON, VEHICLE)

# Rider suppression: persons sitting on these are riders, not pedestrians.
RIDEABLE = {"bicycle", "two_wheeler"}


def normalize_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(label).strip().lower()).strip("_")


def group_of(cls: str) -> str:
    return CANONICAL_CLASSES.get(cls, "other")


def matches_object_types(object_types: list[str] | None, cls: str) -> bool:
    """A policy's object_types may list groups ("vehicle") or classes ("two_wheeler")."""
    if not object_types:
        return True
    wanted = {normalize_label(t) for t in object_types}
    return cls in wanted or group_of(cls) in wanted or "any" in wanted
