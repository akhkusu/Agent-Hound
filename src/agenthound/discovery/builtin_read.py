"""Conservative static model of Claude Code's built-in Read tool.

This evaluates one explicitly supplied settings file, not the effective runtime
policy. Unsupported deny patterns suppress edges rather than assuming access.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from agenthound.models.nodes import Agent, Asset, Capability


def read_capability(agent: Agent, raw: dict[str, Any]) -> Capability | None:
    permissions = raw.get("permissions", {})
    if not isinstance(permissions, dict):
        permissions = {"deny": ["Read(unsupported:policy)"]}
    deny = permissions.get("deny", [])
    if isinstance(deny, list) and any(
        isinstance(rule, str) and "(" not in rule and fnmatch.fnmatchcase("Read", rule)
        for rule in deny
    ):
        return None
    return Capability(
        name="Read", cap_kind="BuiltInTool", agent_scope=agent.objectid,
        file_access="confirmed", file_readonly=True,
        file_evidence="builtin:claude-code:Read; availability inferred from platform, not allow",
        read_config_path=agent.config_path, read_permissions=permissions,
    )


def _inside(path: Path, root: Path) -> bool:
    return path.is_relative_to(root)


def _match(rule: str, kind: str, path: Path, workspace: Path, anchor: Path) -> bool | None:
    if "(" not in rule:
        return fnmatch.fnmatchcase("Read", rule)
    if not rule.startswith("Read("):
        return False
    if not rule.endswith(")"):
        return None
    pattern = rule[5:-1]
    # Full gitignore negation, character classes, parameter rules and foreign
    # path syntax need runtime-aware parsing. Do not silently approximate them.
    if not pattern or any(c in pattern for c in "![]\\:"):
        return None
    if pattern.startswith("//"):
        base, pattern = Path("/"), pattern[2:]
    elif pattern.startswith("~/"):
        base, pattern = Path.home(), pattern[2:]
    elif pattern.startswith("/"):
        base, pattern = anchor, pattern[1:]
    else:
        base = workspace
        pattern = pattern.removeprefix("./")
        if "/" not in pattern or (kind != "allow" and pattern.count("/") == 1):
            pattern = "**/" + pattern
    try:
        relative = path.relative_to(base).as_posix()
    except ValueError:
        return False
    pieces = pattern.split("/")
    if any("**" in part and part != "**" for part in pieces):
        return None
    regex = ""
    for i, part in enumerate(pieces):
        if part == "**":
            regex += "(?:[^/]+/)*" if i < len(pieces) - 1 else ".*"
        else:
            regex += "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in part)
            if i < len(pieces) - 1:
                regex += "/"
    return re.fullmatch(regex, relative) is not None


def read_access(cap: Capability, asset: Asset, workspace: Path) -> dict[str, str] | None:
    """Return suspected reachability evidence, or omit a blocked/unknown edge."""
    workspace = workspace.resolve()
    config = Path(cap.read_config_path).absolute()
    user_config = config == Path.home() / ".claude" / "settings.json"
    project_config = config.parent.name == ".claude" and not user_config
    if project_config and config.parent.parent.resolve() != workspace:
        return None
    anchor = config.parent if user_config or not project_config else workspace
    policy = cap.read_permissions
    requested = Path(asset.path).absolute()
    try:
        resolved = requested.resolve()
    except (OSError, RuntimeError):
        return None
    paths = (requested, resolved)
    matches: dict[str, list[str]] = {"deny": [], "ask": [], "allow": []}
    uncertain = []
    for kind in matches:
        rules = policy.get(kind, [])
        if not isinstance(rules, list):
            if kind == "deny":
                return None
            uncertain.append(f"invalid-{kind}-rules")
            continue
        for rule in rules:
            if not isinstance(rule, str):
                if kind == "deny":
                    return None
                uncertain.append(f"invalid-{kind}-rule")
                continue
            outcomes = [_match(rule, kind, p, workspace, anchor) for p in paths]
            if None in outcomes:
                if kind == "deny":
                    return None
                uncertain.append(f"unsupported-{kind}:{rule}")
            elif (all(outcomes) if kind == "allow" else any(outcomes)):
                matches[kind].append(rule)
    if matches["deny"]:
        return None
    if matches["ask"] and policy.get("defaultMode") == "dontAsk":
        return None
    in_workspace = all(_inside(p, workspace) for p in paths)
    if not in_workspace and not matches["allow"]:
        return None
    if policy.get("blockReadsOutsideWorkingDirectories") and not in_workspace:
        return None
    decision = "ask:" + ",".join(matches["ask"]) if matches["ask"] else (
        "preapproval:" + ",".join(matches["allow"]) if matches["allow"] else "default-workspace-read"
    )
    return {
        "confidence": "suspected",
        "evidence": "; ".join([
            "builtin:claude-code:Read", f"config:{config}", decision,
            "workspace assumed to be session cwd", "single-settings-file; effective policy not merged",
            "runtime approval, trust, hooks, tool restrictions and OS permissions unverified",
            *uncertain,
        ]),
    }
