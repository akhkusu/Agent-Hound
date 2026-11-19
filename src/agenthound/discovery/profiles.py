"""Exact, namespace-aware identity matching and conservative implementation claims.

Only the executed identity is considered. Package installers, option values,
workspace paths, user labels, credentials and URL queries are never identities.
Registry loading is local and never imports or executes third-party MCP code.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit, urlunsplit

from agenthound.discovery.profile_registry import (
    CapabilityProfile,
    load_profiles,
    pypi_name,
)
from agenthound.models.claims import CapabilityClaim


def get_profile(canonical_id: str) -> CapabilityProfile | None:
    return next((p for p in load_profiles() if p.canonical_id == canonical_id), None)


def claims_for_profile(
    profile: CapabilityProfile,
    allowlist: tuple[str, ...] = (),
    denylist: tuple[str, ...] = (),
    *,
    allowlist_configured: bool = False,
    advertised_tools: tuple[str, ...] | None = None,
) -> tuple[CapabilityClaim, ...]:
    claims = []
    for claim in profile.claims:
        tools = tuple(
            t
            for t in claim.tools
            if t not in denylist
            and (not allowlist_configured or t in allowlist)
            and (advertised_tools is None or t in advertised_tools)
        )
        if tools:
            claims.append(claim.model_copy(update={"tools": tools}))
    return tuple(claims)


def legacy_fields(claims: tuple[CapabilityClaim, ...]) -> dict:
    """Project only semantically compatible claims onto the existing axes."""
    fields: dict = {
        key: "none"
        for key in ("file_access", "shell_exec", "network_send", "git_write")
    }
    fields.update(
        file_readonly=False,
        has_shell=False,
        file_evidence="",
        shell_evidence="",
        network_evidence="",
        git_evidence="",
    )
    file_claims = []
    ranks = {"none": 0, "suspected": 1, "confirmed": 2}
    for claim in claims:
        dimension = None
        if claim.resource == "filesystem" and claim.action in {"read", "write"}:
            dimension = "file_access"
            file_claims.append(claim)
        elif claim.resource == "process" and claim.action == "execute":
            dimension = "shell_exec"
        elif claim.resource == "repository" and claim.action in {
            "write",
            "commit",
            "push",
            "merge",
            "create",
            "delete",
        }:
            dimension = "git_write"
        elif (
            claim.resource in {"network", "browser"}
            and claim.scope == "arbitrary_url"
            and claim.action in {"read", "navigate", "send"}
        ):
            dimension = "network_send"
        if dimension:
            if ranks[claim.confidence] > ranks[fields[dimension]]:
                fields[dimension] = claim.confidence
            key = {
                "file_access": "file_evidence",
                "shell_exec": "shell_evidence",
                "network_send": "network_evidence",
                "git_write": "git_evidence",
            }[dimension]
            text = f"{claim.verification_status}:{claim.resource}/{claim.action}; {claim.evidence}; scope:{claim.scope}; {claim.source_url}; runtime access unverified"
            fields[key] = "; ".join(filter(None, (fields[key], text)))[:8000]
    fields["file_readonly"] = bool(file_claims) and all(c.readonly for c in file_claims)
    fields["has_shell"] = fields["shell_exec"] != "none"
    return fields


def _normalize_package(token: str) -> str:
    if token.startswith("@"):
        return "@" + token[1:].split("@", 1)[0]
    return token.split("@", 1)[0]


def _valid_npm_spec(token: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:@[a-z0-9_.-]+/)?[a-z0-9][a-z0-9_.-]*(?:@[a-zA-Z0-9*^~<>=|.+ -]+)?",
            token,
        )
    )


def _normalize_pypi(token: str) -> str | None:
    match = re.fullmatch(
        r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9_,.-]+\])?(?:(?:==|>=|<=|~=|!=|>|<|@)[0-9A-Za-z.*+!<>=~,-]+)?",
        token,
    )
    return pypi_name(match[1]) if match else None


def _executable_basename(command: str) -> str:
    basename = os.path.basename(command.replace("\\", "/")).lower()
    for suffix in (".cmd", ".exe", ".bat"):
        if basename.endswith(suffix):
            return basename[: -len(suffix)]
    return basename


_RUNNER_VALUE_FLAGS = {
    "npx": {"-p", "--package", "--cache", "--loglevel", "--registry", "--userconfig"},
    "bunx": {"-p", "--package", "--backend"},
    "uvx": {
        "-p",
        "--python",
        "--from",
        "--with",
        "--with-requirements",
        "--index",
        "--index-url",
        "--extra-index-url",
        "--constraint",
        "--constraints",
        "--env-file",
        "--cache-dir",
        "--directory",
    },
    "pipx": {"--spec", "--python", "--index-url", "--pip-args"},
}
_RUNNER_BOOL_FLAGS = {
    "npx": {"-y", "--yes", "--no", "--ignore-existing", "--offline", "--quiet", "-q"},
    "bunx": {"-y", "--yes", "--bun", "--no-install", "--silent"},
    "uvx": {
        "--isolated",
        "--offline",
        "--no-cache",
        "--no-progress",
        "--quiet",
        "-q",
        "--verbose",
        "-v",
        "--no-config",
    },
    "pipx": {"--no-cache", "--verbose", "--quiet"},
}
_DOCKER_VALUES = {
    "-e",
    "--env",
    "--env-file",
    "-v",
    "--volume",
    "--mount",
    "-w",
    "--workdir",
    "--name",
    "--network",
    "--user",
    "-u",
    "-p",
    "--publish",
    "--label",
    "--platform",
    "--hostname",
    "--add-host",
    "--cap-add",
    "--cap-drop",
    "--security-opt",
    "--pull",
    "--gpus",
    "--cpus",
    "--memory",
}
_DOCKER_BOOLS = {
    "--rm",
    "-i",
    "-t",
    "-it",
    "-ti",
    "--interactive",
    "--tty",
    "--read-only",
    "--privileged",
    "--init",
    "--detach",
    "-d",
}


def _first_positional(
    args: list[str], start: int, values: set[str], booleans: set[str]
) -> tuple[str, int] | None:
    i = start
    while i < len(args):
        arg = args[i]
        if arg == "--":
            return (args[i + 1], i + 2) if i + 1 < len(args) else None
        if not arg.startswith("-"):
            return arg, i + 1
        flag, sep, _ = arg.partition("=")
        if flag in values:
            if not sep and i + 1 >= len(args):
                return None
            i += 1 if sep else 2
        elif flag in booleans:
            i += 1
        else:
            # Unknown option arity could make its value look like a package.
            return None
    return None


def _executed_identity(
    command: str | None, args: list[str]
) -> tuple[str, str, int] | None:
    if not command:
        return None
    base = _executable_basename(command)
    if base in _RUNNER_VALUE_FLAGS:
        start = 0
        if base == "pipx":
            if not args or args[0] != "run":
                return None
            start = 1
        hit = _first_positional(
            args, start, _RUNNER_VALUE_FLAGS[base], _RUNNER_BOOL_FLAGS[base]
        )
        if hit:
            return ("npm" if base in {"npx", "bunx"} else "pypi"), hit[0], hit[1]
        return None
    if re.fullmatch(r"python(?:[23](?:\.\d+)?)?", base):
        i = 0
        while i < len(args) and args[i] in {"-u", "-B", "-E", "-I", "-s"}:
            i += 1
        if i + 1 < len(args) and args[i] == "-m":
            return "module", args[i + 1], i + 2
        return None
    if base == "docker":
        if not args or args[0] != "run":
            return None
        hit = _first_positional(args, 1, _DOCKER_VALUES, _DOCKER_BOOLS)
        if hit:
            return "container", hit[0], hit[1]
        return None
    return "executable", base, 0


def _container_name(value: str) -> str | None:
    if any(c.isspace() for c in value) or "://" in value:
        return None
    if "@" in value:
        name, digest = value.split("@", 1)
        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            return None
    else:
        name = value
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = name.rsplit(":", 1)
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
            return None
    return name


def sanitized_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        path = "".join(c for c in parsed.path if c.isprintable())
        return urlunsplit((parsed.scheme.lower(), host + port, path, "", ""))
    except ValueError:
        return ""


def _source_specs(command: str | None, args: list[str]) -> list[str]:
    base = _executable_basename(command or "")
    flags = {
        "npx": {"-p", "--package"},
        "bunx": {"-p", "--package"},
        "uvx": {"--from"},
        "pipx": {"--spec"},
    }.get(base, set())
    sources = []
    i = 0
    while i < len(args):
        arg = args[i]
        flag, sep, value = arg.partition("=")
        if flag in flags:
            sources.append(value if sep else (args[i + 1] if i + 1 < len(args) else ""))
            i += 1 if sep else 2
        elif arg == "--" or not arg.startswith("-"):
            # pipx's run subcommand is not a package positional.
            if base == "pipx" and i == 0 and arg == "run":
                i += 1
                continue
            break
        elif flag in _RUNNER_VALUE_FLAGS.get(base, set()):
            i += 1 if sep else 2
        else:
            i += 1
    return sources


def _distribution_matches(profile: CapabilityProfile, token: str, kind: str) -> bool:
    normalized = (
        (_normalize_package(token) if _valid_npm_spec(token) else None)
        if kind == "npm"
        else _normalize_pypi(token)
    )
    return any(
        normalized == (pypi_name(package) if kind == "pypi" else package)
        and profile.package_ecosystems[package] == kind
        for package in profile.packages
    )


def resolve_profile(
    command: str | None, args: list[str], url: str | None = None
) -> tuple[CapabilityProfile, int] | None:
    if url and not command:
        endpoint = sanitized_endpoint(url)
        for profile in load_profiles():
            if endpoint and endpoint in profile.endpoints:
                return profile, 0
        return None
    identity = _executed_identity(command, args)
    if not identity:
        return None
    kind, token, index = identity
    for profile in load_profiles():
        aliases = (*profile.aliases, *profile.compatibility_aliases)
        matched = False
        if kind in {"npm", "pypi"}:
            normalized = (
                (_normalize_package(token) if _valid_npm_spec(token) else None)
                if kind == "npm"
                else _normalize_pypi(token)
            )
            matched = any(
                normalized == (pypi_name(package) if kind == "pypi" else package)
                and profile.package_ecosystems[package] == kind
                for package in profile.packages
            )
            # Installed console scripts may be invoked through a runner.
            sources = _source_specs(command, args)
            matched |= (
                bool(sources)
                and token in profile.executables
                and normalized is not None
            ) or (kind == "npm" and normalized in aliases)
        elif kind == "executable":
            matched = token in {p.lower() for p in profile.executables}
        elif kind == "module":
            matched = token in profile.modules
        elif kind == "container":
            matched = _container_name(token) in profile.containers
        if matched:
            if kind in {"npm", "pypi"}:
                sources = _source_specs(command, args)
                if sources and (
                    len(sources) != 1
                    or not _distribution_matches(profile, sources[0], kind)
                ):
                    continue
            return profile, index
    return None


def compatibility_identity(
    command: str | None, args: list[str], profile: CapabilityProfile
) -> bool:
    identity = _executed_identity(command, args)
    return bool(
        identity
        and identity[0] == "npm"
        and _normalize_package(identity[1]) in profile.compatibility_aliases
    )


def identity_tokens(command: str | None, args: list[str]) -> list[str]:
    identity = _executed_identity(command, args)
    if not identity:
        return []
    kind, token, _ = identity
    # A launcher basename is not an agent-controlled execution tool. Unknown
    # full paths and URLs must not seed name heuristics either.
    if kind == "executable" and token in {
        "bash",
        "sh",
        "zsh",
        "pwsh",
        "powershell",
        "cmd",
    }:
        return []
    if kind in {"npm", "pypi"} and (
        "://" in token or token.startswith(("/", ".", "~")) or "\\" in token
    ):
        return []
    return [token]


def _path(value: str) -> str | None:
    if not value or "://" in value or "\x00" in value or "${" in value:
        return None
    if not (value.startswith(("/", "~", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value)):
        return None
    return os.path.abspath(os.path.expanduser(value))


def extract_allowed_paths(
    args: list[str], payload_index: int
) -> tuple[str, ...] | None:
    """Keep validated roots before an unknown option; never export its value."""
    candidates = []
    for arg in args[payload_index:]:
        if arg in {"--verbose", "--read-only"}:
            continue
        if arg.startswith("-"):
            return tuple(candidates)
        path = _path(arg)
        if path is None:
            return None
        candidates.append(path)
    return tuple(candidates) or None


def extract_repo_scope(args: list[str], payload_index: int) -> tuple[str, ...] | None:
    payload = args[payload_index:]
    i = 0
    positional_safe = True
    while i < len(payload):
        arg = payload[i]
        flag, sep, value = arg.partition("=")
        if flag in {"--repository", "--repo", "-r"}:
            value = value if sep else (payload[i + 1] if i + 1 < len(payload) else "")
            path = _path(value)
            return (path,) if path else None
        if arg.startswith("-"):
            positional_safe = False
        elif positional_safe:
            path = _path(arg)
            return (path,) if path else None
        i += 1
    return None
