"""Collect Impact nodes and Triggers edges from capabilities and connectivity."""

from __future__ import annotations

import socket
import subprocess

from agenthound.collectors.base import CollectionResult
from agenthound.models.edges import Edge
from agenthound.models.nodes import Capability, Impact

_NETWORK_KEYWORDS = ("fetch", "http", "curl", "web", "browse", "url", "request", "remote")
_GIT_KEYWORDS = ("git",)


def check_internet() -> bool:
    """Return True if an outbound TCP connection can be established."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(("8.8.8.8", 53))
        return True
    except OSError:
        return False


def _is_network_cap(cap: Capability) -> bool:
    haystack = " ".join(filter(None, [cap.name.lower(), cap.command or ""])).lower()
    return any(kw in haystack for kw in _NETWORK_KEYWORDS)


def _is_git_cap(cap: Capability) -> bool:
    haystack = " ".join(filter(None, [cap.name.lower(), cap.command or ""])).lower()
    return any(kw in haystack for kw in _GIT_KEYWORDS)


def _has_git_remote(repo_path: str) -> bool:
    """Return True if the git repo at repo_path has any configured remote."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "remote"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(result.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return False


def collect_impact(
    capabilities: list[Capability],
    internet_reachable: bool,
    source_repos: list[str] | None = None,
) -> CollectionResult:
    result = CollectionResult()

    shell_caps = [c for c in capabilities if c.has_shell or c.cap_kind == "ShellHook"]
    if shell_caps:
        impact = Impact(
            name="system-takeover",
            impact_kind="SystemTakeover",
            reachable=True,
            description="Agent can execute arbitrary shell commands",
        )
        result.impacts.append(impact)
        for cap in shell_caps:
            result.edges.append(Edge(start=cap.objectid, end=impact.objectid, kind="Triggers"))

    network_caps = [c for c in capabilities if _is_network_cap(c)]
    if network_caps and internet_reachable:
        impact = Impact(
            name="internet-exfiltration",
            impact_kind="Exfiltration",
            reachable=True,
            description="Agent can send data to external URLs",
        )
        result.impacts.append(impact)
        for cap in network_caps:
            result.edges.append(Edge(start=cap.objectid, end=impact.objectid, kind="Triggers"))

    # SupplyChain only fires when git capability AND a repo with a remote is found
    git_caps = [c for c in capabilities if _is_git_cap(c)]
    repos_with_remote = [r for r in (source_repos or []) if _has_git_remote(r)]
    if git_caps and repos_with_remote:
        impact = Impact(
            name="supply-chain-contamination",
            impact_kind="SupplyChainContamination",
            reachable=True,
            description="Agent can push code to remote repositories",
        )
        result.impacts.append(impact)
        for cap in git_caps:
            result.edges.append(Edge(start=cap.objectid, end=impact.objectid, kind="Triggers"))

    return result
