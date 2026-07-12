import os

from agenthound.discovery.profiles import (
    _normalize_package,
    extract_allowed_paths,
    resolve_profile,
)


def test_resolve_known_package_in_args():
    hit = resolve_profile("npx", ["-y", "@modelcontextprotocol/server-filesystem", "/data"])
    assert hit is not None
    profile, index = hit
    assert profile.package == "@modelcontextprotocol/server-filesystem"
    assert index == 1


def test_resolve_versioned_package():
    hit = resolve_profile("npx", ["-y", "@modelcontextprotocol/server-github@2025.1.1"])
    assert hit is not None
    assert hit[0].package == "@modelcontextprotocol/server-github"


def test_resolve_package_as_command():
    hit = resolve_profile("mcp-server-filesystem", ["/data"])
    assert hit is not None
    profile, index = hit
    assert profile.package == "@modelcontextprotocol/server-filesystem"
    assert index == -1


def test_resolve_unknown_returns_none():
    assert resolve_profile("npx", ["-y", "user-profile-manager-mcp"]) is None


def test_resolve_ignores_registry_name_in_data_args():
    # A registry name appearing as a data value (not the executed package)
    # must not grant a confirmed profile.
    hit = resolve_profile(
        "npx",
        ["-y", "some-unknown-mcp", "--watch", "@modelcontextprotocol/server-github"],
    )
    assert hit is None


def test_resolve_requires_known_runner():
    # Non-runner commands only match via their own basename.
    assert resolve_profile("node", ["@modelcontextprotocol/server-github"]) is None
    assert resolve_profile(None, []) is None


def test_resolve_skips_runner_option_values():
    # "3.12" is the value of --python, not the executed package.
    hit = resolve_profile("uvx", ["--python", "3.12", "mcp-server-fetch"])
    assert hit is not None
    assert hit[0].package == "mcp-server-fetch"

    hit = resolve_profile("npx", ["--cache", "/tmp/cache", "-y",
                                  "@modelcontextprotocol/server-github"])
    assert hit is not None
    assert hit[0].package == "@modelcontextprotocol/server-github"


def test_resolve_matches_package_spec_flag_value():
    hit = resolve_profile("uvx", ["--from", "mcp-server-git", "mcp-server-git"])
    assert hit is not None
    assert hit[0].package == "mcp-server-git"


def test_resolve_option_value_matching_registry_is_not_package():
    # A registry name as the value of a non-package option stays data.
    assert resolve_profile("uvx", ["--env-file", "mcp-server-git", "other-tool"]) is None


def test_normalize_scoped_package_version():
    assert _normalize_package("@scope/name@1.2.3") == "@scope/name"
    assert _normalize_package("plain@1.0") == "plain"
    assert _normalize_package("plain") == "plain"


def test_extract_allowed_paths_skips_flags():
    args = ["-y", "@modelcontextprotocol/server-filesystem", "--verbose", "/a", "/b"]
    assert extract_allowed_paths(args, 1) == ("/a", "/b")


def test_extract_allowed_paths_expands_home():
    args = ["-y", "@modelcontextprotocol/server-filesystem", "~/docs"]
    paths = extract_allowed_paths(args, 1)
    assert paths == (os.path.expanduser("~/docs"),)


def test_extract_allowed_paths_none_when_no_dirs():
    # MCP-roots-only setup: no directory args means scope is unknown.
    args = ["-y", "@modelcontextprotocol/server-filesystem"]
    assert extract_allowed_paths(args, 1) is None
