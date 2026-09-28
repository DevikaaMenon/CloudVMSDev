"""Role-based permission matrix.

Permissions are coarse-grained strings checked on the backend for every
request. Roles are stored in the database (seeded from ROLE_PERMISSIONS on
first start) and users can hold several roles; effective permissions are the
union of their roles. Camera-level scoping (which cameras a user may see) is
handled separately via ``User.camera_scope``.
"""
from __future__ import annotations

ALL_PERMISSIONS: dict[str, str] = {
    "users:manage": "Create users, assign roles and camera scope",
    "audit:view": "Read the audit log",
    "settings:manage": "Change system-wide settings (detector profile, retention)",
    "cameras:view": "See cameras and their health",
    "cameras:manage": "Register, edit, delete cameras and change analytics config",
    "streams:control": "Start and stop camera streams",
    "live:view": "Watch live video",
    "recordings:view": "Browse and play recordings",
    "zones:view": "See zones and policies",
    "zones:manage": "Create, edit and delete zones and policies",
    "events:view": "Search and view incidents and evidence",
    "events:update": "Acknowledge, investigate, resolve or dismiss incidents",
    "analytics:view": "View statistics and performance dashboards",
    "models:manage": "Register and activate models, upload datasets, start training",
    "system:view": "View worker and resource health",
}

ROLE_PERMISSIONS: dict[str, dict] = {
    "admin": {
        "description": "Full control of the system",
        "permissions": sorted(ALL_PERMISSIONS),
    },
    "operator": {
        "description": "Monitors assigned cameras and handles incidents",
        "permissions": [
            "cameras:view", "streams:control", "live:view", "recordings:view",
            "zones:view", "events:view", "events:update", "analytics:view", "system:view",
        ],
    },
    "zone_manager": {
        "description": "Add-on role: may configure zones and policies (give together with operator)",
        "permissions": ["zones:view", "zones:manage", "cameras:view"],
    },
    "viewer": {
        "description": "Read-only access to permitted cameras, events and playback",
        "permissions": [
            "cameras:view", "live:view", "recordings:view", "zones:view",
            "events:view", "analytics:view",
        ],
    },
}
