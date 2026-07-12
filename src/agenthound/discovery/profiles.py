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


def resolve_profile(
    command: str | None, args: list[str]
) -> tuple[CapabilityProfile, int] | None:
    """Match command basename or any arg against the registry.

    Returns (profile, index) where index is the position of the matched arg,
    or -1 when the command itself matched (all args follow the package).
    """
    if command and os.path.basename(command) in _REGISTRY:
        return _REGISTRY[os.path.basename(command)], -1
    for idx, arg in enumerate(args):
        profile = _REGISTRY.get(_normalize_package(arg))
        if profile is not None:
            return profile, idx
    return None


def extract_allowed_paths(args: list[str], package_index: int) -> tuple[str, ...] | None:
    """Extract allowed directories following the package name argument.

    Only meaningful for scoped_paths profiles (server-filesystem style, where
    every positional arg after the package is an allowed directory). Flags are
    skipped; paths are normalized but not checked for existence, since the
    config may describe another host. Returns None (scope unknown) when no
    directory args are present — e.g. an MCP-roots-only setup.
    """
    candidates = [a for a in args[package_index + 1:] if not a.startswith("-")]
    if not candidates:
        return None
    return tuple(os.path.abspath(os.path.expanduser(a)) for a in candidates)
