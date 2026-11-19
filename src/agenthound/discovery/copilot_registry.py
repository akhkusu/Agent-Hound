"""Read Copilot's known HKCU settings without enumerating credential values."""

from __future__ import annotations

import importlib
from typing import Any

_ROOT = r"Software\Microsoft\Copilot\SharedStorage"
_KEYS = {
    "Auth": (),
    "FeatureConfigs": ("isAutopilotNodeEnabled",),
    "FeatureGates": (
        "codeTabGates_codeTabEnabled",
        "oneAuthNativeGates_oneAuthNativeEnabled",
    ),
    "FeatureSettings": (),
    "LocalSettings": (),
    "Profiles": (),
    "UserEligibility": (),
}


def inventory_copilot_registry() -> dict[str, Any]:
    """Observe current user's live registry; never read arbitrary value bodies.

    Explicitly requested separately from offline filesystem inventory. The
    returned booleans are cached feature flags, not effective capabilities.
    """
    try:
        registry = importlib.import_module("winreg")
    except ImportError:
        return {"status": "unsupported_platform", "keys": []}
    entries = []
    for suffix, allowed_names in _KEYS.items():
        path = _ROOT + "\\" + suffix
        item: dict[str, Any] = {"path": "HKCU\\" + path}
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, path) as key:
                subkeys, values, _ = registry.QueryInfoKey(key)
                flags = {}
                for name in allowed_names:
                    try:
                        value, kind = registry.QueryValueEx(key, name)
                    except FileNotFoundError:
                        continue
                    if kind == registry.REG_DWORD and type(value) is int and value in (0, 1):
                        flags[name] = bool(value)
                item.update(
                    status="present", subkey_count=subkeys, value_count=values,
                    cached_feature_booleans=flags,
                )
        except FileNotFoundError:
            item["status"] = "missing"
        except OSError:
            item["status"] = "unreadable"
        entries.append(item)
    return {"status": "observed", "keys": entries}
