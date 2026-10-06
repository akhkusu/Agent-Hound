"""Observe the owner SID of a local Claude Code process."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone


_SID = re.compile(r"S-1-(?:\d+-)+\d+")


class RuntimeIdentityError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeIdentity:
    pid: int
    owner_sid: str
    observed_at: str
    computer_sid: str | None = None


def observe_claude_pid(pid: int | None = None) -> RuntimeIdentity:
    if sys.platform != "win32":
        raise RuntimeIdentityError(
            "--process-identity and --claude-pid require native Windows; "
            "omit them for a config-only scan on Linux or macOS"
        )
    if pid is not None and pid <= 0:
        raise RuntimeIdentityError("Claude process PID must be positive")
    process_filter = f"ProcessId = {pid}" if pid is not None else "Name = 'claude.exe'"
    script = (
        f"$processes = @(Get-CimInstance Win32_Process -Filter \"{process_filter}\"); "
        "if ($processes.Count -ne 1) { exit 2 }; "
        "$process = $processes[0]; "
        "if ($null -eq $process -or $process.Name -ne 'claude.exe') { exit 2 }; "
        "$owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid; "
        "if ($owner.ReturnValue -ne 0 -or [string]::IsNullOrWhiteSpace($owner.Sid)) { exit 3 }; "
        "$computerSid = $null; "
        "try { "
        "$system = Get-CimInstance Win32_ComputerSystem; "
        "if ($system.PartOfDomain) { "
        "$account = [System.Security.Principal.NTAccount]::new($system.Domain, ($env:COMPUTERNAME + '$')); "
        "$computerSid = $account.Translate([System.Security.Principal.SecurityIdentifier]).Value "
        "} } catch { $computerSid = $null }; "
        "[pscustomobject]@{ pid = $process.ProcessId; name = $process.Name; "
        "sid = $owner.Sid; computer_sid = $computerSid } | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeIdentityError("Could not query the Claude process") from exc
    if result.returncode != 0:
        raise RuntimeIdentityError("Expected exactly one readable claude.exe process; use --claude-pid if multiple are running")
    try:
        observed = json.loads(result.stdout)
        if (pid is not None and observed["pid"] != pid) or observed["name"].lower() != "claude.exe":
            raise ValueError("process changed during observation")
        sid = observed["sid"]
        if not isinstance(observed["pid"], int) or not isinstance(sid, str) or _SID.fullmatch(sid) is None:
            raise ValueError("invalid owner SID")
        computer_sid = observed.get("computer_sid")
        if computer_sid is not None and (not isinstance(computer_sid, str) or _SID.fullmatch(computer_sid) is None):
            raise ValueError("invalid computer SID")
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeIdentityError("Invalid Claude process identity response") from exc
    return RuntimeIdentity(pid=observed["pid"], owner_sid=sid, observed_at=datetime.now(timezone.utc).isoformat(), computer_sid=computer_sid)
