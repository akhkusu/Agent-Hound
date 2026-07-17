"""Collect Asset nodes and CanAccess edges from the filesystem."""

from __future__ import annotations

from pathlib import Path

from agenthound.collectors.base import CollectionResult
from agenthound.discovery.workspace import find_asset_files
from agenthound.models.edges import Edge
from agenthound.models.nodes import Asset, Capability


def _is_privileged(cap: Capability) -> bool:
    if cap.shell_exec != "none" or cap.cap_kind == "ShellHook":
        return True
    return cap.file_access != "none"


def _in_allowed_paths(asset_path: str, allowed_paths: tuple[str, ...]) -> bool:
    """True if asset_path is inside one of the allowed directories.

    Uses resolved paths with relative_to() so "/tmp/docs2" is never treated
    as inside "/tmp/docs". Symlinked assets resolve to their target before
    comparison, which matches what the server itself can reach.
    """
    resolved = Path(asset_path).resolve()
    for allowed in allowed_paths:
        try:
            resolved.relative_to(Path(allowed).resolve())
            return True
        except ValueError:
            continue
    return False


def _can_access(cap: Capability, asset: Asset) -> bool:
    # CanAccess models read reachability: even a read-only file capability can
    # read source code, so `writable` does not gate the edge (a prior version
    # wrongly skipped writable assets for read-only caps).
    # Shell capabilities can reach any path; file scoping only constrains
    # capabilities whose access comes from a scoped file server.
    if cap.shell_exec != "none" or cap.cap_kind == "ShellHook":
        return True
    if cap.allowed_paths is not None:
        return _in_allowed_paths(asset.path, cap.allowed_paths)
    return True


def _edge_properties(cap: Capability) -> dict[str, str]:
    confidence: str
    if cap.shell_exec != "none" or cap.cap_kind == "ShellHook":
        confidence, evidence = cap.shell_exec, cap.shell_evidence
    else:
        confidence, evidence = cap.file_access, cap.file_evidence
        if cap.allowed_paths is not None:
            # Static args can be replaced at runtime via MCP roots, so a
            # scoped claim is a strong estimate, not a verified fact.
            confidence = "suspected"
            evidence = (evidence + "; " if evidence else "") + "scoped-by-args"
        else:
            # No scope info at all (e.g. roots-only filesystem server): the
            # package is confirmed but reaching *this* asset is a guess.
            confidence = "suspected"
            evidence = (evidence + "; " if evidence else "") + "scope-unknown"
    props: dict[str, str] = {"confidence": confidence}
    if evidence:
        props["evidence"] = evidence
    return props


def collect_assets(capabilities: list[Capability], workspace: Path, scope: str) -> CollectionResult:
    result = CollectionResult()
    raw = find_asset_files(scope=scope, workspace=workspace)
    seen: set[str] = set()

    privileged = [c for c in capabilities if _is_privileged(c)]

    for path_str, kind in raw:
        fpath = Path(path_str)
        name = fpath.name or fpath.resolve().name or path_str
        # writable=True only for SourceCode; other assets (SSHKey, EnvFile, etc.) are
        # marked False here even though shell-capable agents could write them.
        # The CanAccess edge covers the reachability — writable reflects file intent.
        asset = Asset(
            name=name,
            asset_kind=kind,
            path=path_str,
            readable=True,
            writable=kind == "SourceCode",
        )
        if asset.objectid not in seen:
            result.assets.append(asset)
            seen.add(asset.objectid)
            for cap in privileged:
                if not _can_access(cap, asset):
                    continue
                result.edges.append(Edge(
                    start=cap.objectid,
                    end=asset.objectid,
                    kind="CanAccess",
                    properties=_edge_properties(cap),
                ))

    return result
