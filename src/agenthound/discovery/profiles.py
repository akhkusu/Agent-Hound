"""Capability profiles for known MCP server packages.

A profile records what a package can actually do, verified against its
official README / source (see the PR that introduced each entry). Matching a
profile yields "confirmed" confidence because the package identity is a fact
stated in the config file — unlike name-keyword matching, which stays
"suspected".

Limitations: packages launched through wrappers that hide the package name
from args (e.g. ``docker run image``) are not recognized and fall back to
keyword heuristics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from agenthound.models.nodes import Confidence


@dataclass(frozen=True)
class CapabilityProfile:
    package: str
    file_access: Confidence = "none"
    file_readonly: bool = False
    # Extract allowed directories from the args after the package name
    # (server-filesystem style invocation).
    scoped_paths: bool = False
    shell_exec: Confidence = "none"
    network_send: Confidence = "none"
    git_write: Confidence = "none"
    note: str = ""
    aliases: tuple[str, ...] = field(default=())


_PROFILES = [
    CapabilityProfile(
        package="@modelcontextprotocol/server-filesystem",
        file_access="confirmed",
        scoped_paths=True,
        note="read/write within allowed directories; MCP roots may replace them",
        aliases=("mcp-server-filesystem",),
    ),
    CapabilityProfile(
        package="@modelcontextprotocol/server-github",
        git_write="confirmed",
        # Fixed API endpoint, but issues/PRs are writable on arbitrary repos,
        # which is a plausible (not arbitrary-URL) exfiltration channel.
        network_send="suspected",
        note="push_files / create_or_update_file via GitHub API",
    ),
    CapabilityProfile(
        package="mcp-server-git",
        git_write="confirmed",
        note="local commits only (no push tool)",
        aliases=("@modelcontextprotocol/server-git",),
    ),
    CapabilityProfile(
        package="mcp-server-fetch",
        network_send="confirmed",
        note="fetches arbitrary URLs",
        aliases=("@modelcontextprotocol/server-fetch",),
    ),
    CapabilityProfile(
        package="@modelcontextprotocol/server-puppeteer",
        network_send="confirmed",
        note="navigates a browser to arbitrary URLs",
    ),
    # Explicitly benign entries: matching one of these suppresses the keyword
    # fallback, so e.g. "memory" or a search tool never gets guessed flags.
    CapabilityProfile(
        package="@modelcontextprotocol/server-brave-search",
        note="search queries to a fixed API endpoint; not an arbitrary send channel",
    ),
    CapabilityProfile(
        package="@modelcontextprotocol/server-memory",
        note="local knowledge-graph memory",
    ),
    CapabilityProfile(
        package="@wonderwhy-er/desktop-commander",
        shell_exec="confirmed",
        file_access="confirmed",
        note="start_process + unrestricted file read/write",
    ),
    CapabilityProfile(
        package="mcp-server-commands",
        shell_exec="confirmed",
        note="run_process executes arbitrary commands",
    ),
]

_REGISTRY: dict[str, CapabilityProfile] = {}
for _profile in _PROFILES:
    _REGISTRY[_profile.package] = _profile
    for _alias in _profile.aliases:
        _REGISTRY[_alias] = _profile


def _normalize_package(token: str) -> str:
    """Strip a version suffix from an npm-style package spec."""
    if token.startswith("@"):
        # "@scope/name@1.2.3" -> "@scope/name"
        rest = token[1:]
        if "@" in rest:
            rest = rest.split("@", 1)[0]
        return "@" + rest
    return token.split("@", 1)[0]


# Runners whose first positional argument names the package to execute,
# mapped to the flags that consume a following value (so an option value is
# never mistaken for the executed package, e.g. "uvx --python 3.12 <pkg>").
_RUNNER_VALUE_FLAGS: dict[str, set[str]] = {
    "npx": {"-p", "--package", "-c", "--call", "--cache", "--loglevel",
            "--registry", "--userconfig"},
    "uvx": {"-p", "--python", "--from", "--with", "--with-requirements",
            "--index", "--index-url", "--extra-index-url", "--constraint",
            "--constraints", "--env-file", "--cache-dir"},
    "bunx": set(),
}


def resolve_profile(
    command: str | None, args: list[str]
) -> tuple[CapabilityProfile, int] | None:
    """Match the executed package against the registry.

    Only the command basename or — for known runners like npx/uvx — the first
    positional argument is considered, because that is the one thing the
    config proves is executed. Package-spec flags (npx -p, uvx --from) only
    add a package to the environment and do not guarantee the launched binary
    comes from it, so their values never grant a profile; the honest cases
    still match because binary names are registered as aliases (e.g.
    "mcp-server-filesystem"). Other args are data (paths, option values) and
    must never grant a confirmed profile either.

    Returns (profile, payload_index) where payload_index is the position in
    args where the executed package's own arguments begin.
    """
    if command is None:
        return None
    basename = os.path.basename(command)
    if basename in _REGISTRY:
        return _REGISTRY[basename], 0
    value_flags = _RUNNER_VALUE_FLAGS.get(basename)
    if value_flags is None:
        return None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("-"):
            flag, _, inline_value = arg.partition("=")
            if not inline_value and flag in value_flags:
                i += 2
                continue
            i += 1
            continue
        # First true positional arg is what actually executes.
        profile = _REGISTRY.get(_normalize_package(arg))
        return (profile, i + 1) if profile is not None else None
    return None


def extract_allowed_paths(args: list[str], payload_index: int) -> tuple[str, ...] | None:
    """Extract allowed directories from the executed package's own arguments.

    Only meaningful for scoped_paths profiles (server-filesystem style, where
    every positional arg after the package is an allowed directory). Flags are
    skipped; paths are normalized but not checked for existence, since the
    config may describe another host. Returns None (scope unknown) when no
    directory args are present — e.g. an MCP-roots-only setup.
    """
    candidates = [a for a in args[payload_index:] if not a.startswith("-")]
    if not candidates:
        return None
    return tuple(os.path.abspath(os.path.expanduser(a)) for a in candidates)
