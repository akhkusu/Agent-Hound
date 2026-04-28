"""BloodHound custom_types definitions for all Agent-Hound node kinds."""

from __future__ import annotations

from typing import Any

CUSTOM_TYPES: dict[str, Any] = {
    "Agent":      {"icon": {"name": "robot",       "type": "font-awesome", "color": "#e74c3c"}},
    "Source":     {"icon": {"name": "file-import",  "type": "font-awesome", "color": "#f39c12"}},
    "Capability": {"icon": {"name": "bolt",         "type": "font-awesome", "color": "#9b59b6"}},
    "Asset":      {"icon": {"name": "lock",         "type": "font-awesome", "color": "#e67e22"}},
    "Impact":     {"icon": {"name": "skull",        "type": "font-awesome", "color": "#c0392b"}},
}
