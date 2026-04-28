"""Collect Asset nodes and CanAccess edges from the filesystem."""

from __future__ import annotations

from pathlib import Path

from agenthound.collectors.base import CollectionResult
from agenthound.discovery.workspace import find_asset_files
from agenthound.models.edges import Edge
from agenthound.models.nodes import Asset, Capability

_FILESYSTEM_INDICATORS = ("filesystem", "file", "read_file", "write_file", "search_files")
_READONLY_INDICATORS = ("read_file", "read", "list", "search", "get", "fetch_content", "view")


def _is_privileged(cap: Capability) -> bool:
    if cap.has_shell:
        return True
    if cap.cap_kind == "ShellHook":
        return True
    name_lower = cap.name.lower()
    return any(kw in name_lower for kw in _FILESYSTEM_INDICATORS)


def _is_readonly_cap(cap: Capability) -> bool:
    name_lower = cap.name.lower()
    # Only mark as read-only if explicitly read-named AND not also shell-privileged
    if cap.has_shell or cap.cap_kind == "ShellHook":
        return False
    return any(
        name_lower.startswith(kw) or name_lower == kw
        for kw in _READONLY_INDICATORS
    )


def collect_assets(capabilities: list[Capability], workspace: Path, scope: str) -> CollectionResult:
    result = CollectionResult()
    raw = find_asset_files(scope=scope, workspace=workspace)
    seen: set[str] = set()

    privileged = [c for c in capabilities if _is_privileged(c)]

    for path_str, kind in raw:
        fpath = Path(path_str)
        # writable=True only for SourceCode; other assets (SSHKey, EnvFile, etc.) are
        # marked False here even though shell-capable agents could write them.
        # The CanAccess edge covers the reachability — writable reflects file intent.
        asset = Asset(
            name=fpath.name,
            asset_kind=kind,
            path=path_str,
            readable=True,
            writable=kind == "SourceCode",
        )
        if asset.objectid not in seen:
            result.assets.append(asset)
            seen.add(asset.objectid)
            for cap in privileged:
                # Read-only capabilities don't get CanAccess to writable assets
                if _is_readonly_cap(cap) and asset.writable:
                    continue
                result.edges.append(Edge(start=cap.objectid, end=asset.objectid, kind="CanAccess"))

    return result
