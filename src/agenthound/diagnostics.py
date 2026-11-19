"""Read-only, offline checks for local collection prerequisites."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
import os
from pathlib import Path
import sys
from typing import Literal

from agenthound.discovery.config_parser import discover_config_files, parse_config


@dataclass(frozen=True)
class DiagnosticFinding:
    status: Literal["ok", "warning", "error"]
    check: str
    message: str
    path: Path | None = None


def _configuration_findings(workspace: Path, config: Path | None) -> list[DiagnosticFinding]:
    try:
        with os.scandir(workspace):
            pass
        if not os.access(workspace, os.R_OK | os.X_OK):
            raise PermissionError
    except (OSError, ValueError):
        return [DiagnosticFinding(
            "error", "workspace",
            "Workspace must be an existing, readable directory. Choose a directory you can list and access.",
            workspace,
        )]

    findings = [DiagnosticFinding("ok", "workspace", "Workspace directory is accessible.", workspace)]
    try:
        paths = [config] if config is not None else discover_config_files(workspace)
    except (OSError, ValueError):
        findings.append(DiagnosticFinding(
            "error", "configuration", "Could not inspect configuration locations. Check directory permissions."
        ))
        return findings
    if not paths:
        findings.append(DiagnosticFinding(
            "warning", "configuration",
            "No supported agent configurations found. Configure an agent or pass --config FILE to inspect an existing file.",
        ))
    for path in paths:
        try:
            agent, _ = parse_config(path)
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            # Parser and Pydantic messages can contain configuration values.
            # Deliberately report only the path and a fixed remediation message.
            findings.append(DiagnosticFinding(
                "error", "configuration",
                "Configuration could not be parsed. Check file readability, syntax and supported value types locally.",
                path,
            ))
        else:
            findings.append(DiagnosticFinding(
                "ok", "configuration",
                f"{agent.platform} configuration parsed; runtime enablement and access are unverified.",
                path,
            ))
    return findings


def _package_finding(check: str, packages: tuple[str, ...], extra: str) -> DiagnosticFinding:
    try:
        for package in packages:
            metadata.version(package)
    except metadata.PackageNotFoundError:
        return DiagnosticFinding(
            "warning", check,
            f"Optional dependencies missing. Install with: pip install 'agenthound[{extra}]'.",
        )
    except (OSError, ValueError):
        return DiagnosticFinding(
            "warning", check,
            f"Could not inspect optional package metadata. Check the 'agenthound[{extra}]' installation.",
        )
    return DiagnosticFinding("ok", check, "Optional package metadata found; imports and runtime use are unverified.")


def run_diagnostics(workspace: Path, config: Path | None = None) -> list[DiagnosticFinding]:
    """Check local paths, static parsing and package metadata without live collection.

    Supplying ``config`` limits parsing to that file. These checks never start
    MCP servers, request authentication, probe network access or write output.
    """
    findings = _configuration_findings(workspace, config)
    if sys.platform == "win32":
        findings.append(DiagnosticFinding(
            "ok", "platform",
            "Windows supports runtime identity, registry and WAM collection; permissions and authentication are unverified.",
        ))
    else:
        findings.append(DiagnosticFinding(
            "ok", "platform",
            f"Platform: {sys.platform}. Windows runtime identity, registry and WAM collection require Windows.",
        ))
    findings.append(_package_finding("mcp", ("mcp",), "mcp"))
    if sys.platform == "win32":
        findings.append(_package_finding("windows-auth", (
            "winrt-Windows.Security.Authentication.Web.Core",
            "winrt-Windows.Security.Credentials",
            "winrt-Windows.System",
        ), "windows-auth"))
    return findings
