"""Collect Impact nodes and Triggers edges from capabilities and connectivity."""

from __future__ import annotations

import socket
import subprocess

from agenthound.collectors.base import CollectionResult
from agenthound.models.edges import Edge
from agenthound.models.nodes import Capability, Impact

def check_internet() -> bool:
    """Return True if an outbound TCP connection can be established."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(("8.8.8.8", 53))
        return True
    except OSError:
        return False


def _trigger_properties(confidence: str, evidence: str) -> dict[str, str]:
    props = {"confidence": confidence}
    if evidence:
        props["evidence"] = evidence
    return props


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

    shell_caps = [c for c in capabilities if c.shell_exec != "none" or c.cap_kind == "ShellHook"]
    if shell_caps:
        impact = Impact(
            name="system-takeover",
            impact_kind="SystemTakeover",
            reachable=True,
            description="Agent can execute arbitrary shell commands",
        )
        result.impacts.append(impact)
        for cap in shell_caps:
            result.edges.append(Edge(
                start=cap.objectid, end=impact.objectid, kind="Triggers",
                properties=_trigger_properties(cap.shell_exec, cap.shell_evidence),
            ))

    network_caps = [c for c in capabilities if c.network_send != "none"]
    if network_caps and internet_reachable:
        impact = Impact(
            name="internet-exfiltration",
            impact_kind="Exfiltration",
            reachable=True,
            description="Agent can send data to external URLs",
        )
        result.impacts.append(impact)
        for cap in network_caps:
            result.edges.append(Edge(
                start=cap.objectid, end=impact.objectid, kind="Triggers",
                properties=_trigger_properties(cap.network_send, cap.network_evidence),
            ))

    # SupplyChain only fires when git write capability AND a repo with a remote is found
    git_caps = [c for c in capabilities if c.git_write != "none"]
    repos_with_remote = [r for r in (source_repos or []) if _has_git_remote(r)]
    if git_caps and repos_with_remote:
        impact = Impact(
            name="supply-chain-contamination",
            impact_kind="SupplyChainContamination",
            reachable=True,
            description="Agent can write commits to repositories with configured remotes",
        )
        result.impacts.append(impact)
        for cap in git_caps:
            result.edges.append(Edge(
                start=cap.objectid, end=impact.objectid, kind="Triggers",
                properties=_trigger_properties(cap.git_write, cap.git_evidence),
            ))

    return result
