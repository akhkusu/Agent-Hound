"""Inventory Windows Copilot data locations without collecting browser secrets."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agenthound.models.nodes import Agent

POLICY_KEYS = (
    "isCPSResponseSuccessful",
    "isCopilotFileUploadEnabledWeb",
    "isCopilotFileUploadEnabledWork",
    "isCopilotWebSearchEnabledWeb",
    "isCopilotWebSearchEnabledWork",
)
_PROFILE = re.compile(r"Default|Profile [0-9]+")
_MAX_SETTINGS_BYTES = 4 * 1024 * 1024
FILE_PERMISSION_KEYS = ("is_file_search_allowed", "is_file_read_allowed")


def _file_permissions(settings: dict[str, Any]) -> dict[str, str]:
    wcp = settings.get("wcp")
    search = wcp.get("search_app_data") if isinstance(wcp, dict) else None
    return {
        key: (
            "cached_allowed" if search[key] else "cached_denied"
        ) if isinstance(search, dict) and type(search.get(key)) is bool else "unknown"
        for key in FILE_PERMISSION_KEYS
    }


def copilot_data_roots(local_app_data: Path) -> tuple[tuple[str, Path], ...]:
    """Known browser roots, including the separate authentication WebView."""
    main = local_app_data / "Microsoft/Copilot/User Data"
    return (
        ("copilot-windows", main),
        ("copilot-auth-webview", main / "OneAuth/WebView2/EBWebView"),
        (
            "officehub-webview",
            local_app_data / "Packages"
            / "Microsoft.MicrosoftOfficeHub_8wekyb3d8bbwe/LocalState/EBWebView",
        ),
    )


def _presence(path: Path) -> dict[str, str]:
    try:
        path.stat()
        status = "present"
    except FileNotFoundError:
        status = "missing"
    except OSError:
        status = "unreadable"
    return {"path": str(path), "status": status}


def _settings(path: Path) -> tuple[dict[str, Any], str]:
    try:
        if not path.is_file():
            return {}, "missing"
        if path.stat().st_size > _MAX_SETTINGS_BYTES:
            return {}, "too_large"
        with path.open("rb") as stream:
            contents = stream.read(_MAX_SETTINGS_BYTES + 1)
        if len(contents) > _MAX_SETTINGS_BYTES:
            return {}, "too_large"
        value = json.loads(contents.decode("utf-8-sig"))
        if not isinstance(value, dict):
            return {}, "invalid_object"
        return value, "read"
    except (OSError, UnicodeError, ValueError):
        return {}, "unreadable_or_invalid"


def inventory_copilot_windows(
    local_app_data: Path | None = None, *, include_registry: bool = False,
) -> dict[str, Any]:
    """Return observed data roots and allowlisted cached policy booleans.

    Data presence is not installation, sign-in, license or effective access
    evidence. Preferences are never interpreted as MCP or instruction files.
    """
    if local_app_data is None:
        location = os.environ.get("LOCALAPPDATA")
        if not location:
            raise ValueError("LOCALAPPDATA is unset; pass --local-app-data explicitly")
        local_app_data = Path(location)
    roots = copilot_data_roots(local_app_data)
    observations: list[dict[str, Any]] = []
    for product, root in roots:
        try:
            if not root.is_dir():
                continue
            profiles = sorted(
                p for p in root.iterdir() if _PROFILE.fullmatch(p.name) and p.is_dir()
            )
        except OSError:
            observations.append(
                {"product": product, "data_root": str(root), "status": "unreadable"}
            )
            continue
        state, state_status = _settings(root / "Local State")
        entries = []
        for profile in profiles:
            prefs, status = _settings(profile / "Preferences")
            copilot = prefs.get("copilot", {})
            policies = (
                copilot.get("ocps_policies", {}) if isinstance(copilot, dict) else {}
            )
            cached = {
                key: policies[key]
                for key in POLICY_KEYS
                if isinstance(policies, dict) and type(policies.get(key)) is bool
            }
            entries.append(
                {
                    "profile": profile.name,
                    "preferences_path": str(profile / "Preferences"),
                    "status": status,
                    "cached_policy_booleans": cached,
                    "local_file_permission_settings": _file_permissions(prefs),
                    "authentication_store_presence": {
                        name: _presence(profile / relative)
                        for name, relative in (
                            ("cookies", "Network/Cookies"),
                            ("device_bound_sessions", "Network/Device Bound Sessions"),
                            ("saved_logins", "Login Data"),
                        )
                    },
                }
            )
        observations.append(
            {
                "product": product,
                "data_root": str(root),
                "status": "data_present",
                "local_state_path": str(root / "Local State"),
                "local_state_status": state_status,
                "local_file_permission_settings": _file_permissions(state),
                "profiles": entries,
            }
        )
    result: dict[str, Any] = {
        "schema_version": 2,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "observations": observations,
        "shared_authentication_store_presence": {
            name: _presence(local_app_data / relative)
            for name, relative in (
                ("oneauth_accounts", "Microsoft/OneAuth/accounts"),
                ("oneauth_blobs", "Microsoft/OneAuth/blobs"),
                ("identity_cache", "Microsoft/IdentityCache"),
                ("token_broker", "Microsoft/TokenBroker"),
                ("aad_broker", "Packages/Microsoft.AAD.BrokerPlugin_cw5n1h2txyewy/AC/TokenBroker"),
            )
        },
        "registry": {"status": "not_requested", "keys": []},
        "file_access_assessment": {
            "local_file_search": "unverified",
            "local_file_read": "unverified",
            "sharepoint_retrieval": "unverified",
            "reason": "No runtime tool invocation or document retrieval evidence collected.",
            "settings_basis": "wcp.search_app_data.is_file_search_allowed / is_file_read_allowed",
        },
        "limitations": [
            "Data presence does not prove installation or active sign-in.",
            "Cached policies do not establish effective capabilities or SharePoint access.",
            "Cloud agents and connected sources require separate evidence.",
            "Authentication-store presence does not prove a valid token or app ownership.",
            "Registry observations describe the current Windows user, not an offline copy.",
            "Missing file-search/read settings do not imply allowed or denied defaults.",
        ],
    }
    if include_registry:
        from agenthound.discovery.copilot_registry import inventory_copilot_registry

        result["registry"] = inventory_copilot_registry()
    return result


def copilot_inventory_graph(inventory: dict[str, Any]) -> dict[str, Any]:
    """Export data-presence evidence without inventing capabilities or paths."""
    nodes = []
    for item in inventory["observations"]:
        if item["status"] != "data_present" or item["product"] == "copilot-auth-webview":
            continue
        node = Agent(
            name=item["product"],
            platform="Microsoft Copilot on Windows"
            if item["product"] == "copilot-windows"
            else "Microsoft OfficeHub WebView",
            config_path=item["data_root"],
        ).to_opengraph()
        node["properties"].update(
            observation_kind="local_data_presence",
            confidence="confirmed",
            observed_at=inventory["observed_at"],
            evidence="Known application data root observed; runtime and cloud access unverified",
        )
        nodes.append(node)
    return {
        "metadata": {"source_kind": "AgentHound"},
        "graph": {"nodes": nodes, "edges": []},
    }
