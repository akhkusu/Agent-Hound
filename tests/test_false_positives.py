"""Regression tests for the false-positive patterns listed in issue #1.

Each test reproduces one FP scenario end-to-end through the public API
(config file -> parse_config -> collectors), so they fail on the old
substring-matching heuristics and pass once detection is evidence-based.
"""

import json
import subprocess

from agenthound.collectors.assets import collect_assets
from agenthound.collectors.impact import collect_impact
from agenthound.discovery.config_parser import parse_config


def _parse_servers(tmp_path, servers):
    config_path = tmp_path / "mcp_config.json"
    config_path.write_text(json.dumps({"mcpServers": servers}))
    _, caps = parse_config(config_path)
    return caps


def _git_repo_with_remote(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/test/repo.git"],
        capture_output=True,
    )
    return repo


# FP-1: filesystem server must not reach assets outside its allowed directories


def test_fp1_filesystem_scoped_to_allowed_directory(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / ".env").write_text("SECRET=in-scope")
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("out-of-scope key")

    caps = _parse_servers(tmp_path, {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", str(docs)],
        },
    })
    result = collect_assets(capabilities=caps, workspace=tmp_path, scope="workspace")

    reachable = {
        next(a.path for a in result.assets if a.objectid == e.end)
        for e in result.edges
        if e.kind == "CanAccess"
    }
    assert str(docs / ".env") in reachable
    assert str(ssh / "id_rsa") not in reachable


# FP-2: a server merely containing "file" in its name is not a filesystem tool


def test_fp2_profile_manager_gets_no_file_access(tmp_path):
    (tmp_path / ".env").write_text("SECRET=x")

    caps = _parse_servers(tmp_path, {
        "profile-manager": {
            "command": "npx",
            "args": ["-y", "user-profile-manager-mcp"],
        },
    })
    result = collect_assets(capabilities=caps, workspace=tmp_path, scope="workspace")

    assert [e for e in result.edges if e.kind == "CanAccess"] == []


# FP-3: generic words (command/run) must not mark a server as shell-capable


def test_fp3_command_palette_is_not_shell(tmp_path):
    caps = _parse_servers(tmp_path, {
        "command-palette": {
            "command": "npx",
            "args": ["-y", "command-palette-mcp"],
        },
    })
    result = collect_impact(capabilities=caps, internet_reachable=True)

    kinds = {i.impact_kind for i in result.impacts}
    assert "SystemTakeover" not in kinds


def test_fp3_run_tasks_is_not_shell(tmp_path):
    caps = _parse_servers(tmp_path, {
        "run-tasks": {
            "command": "npx",
            "args": ["-y", "run-tasks-mcp"],
        },
    })
    result = collect_impact(capabilities=caps, internet_reachable=True)

    kinds = {i.impact_kind for i in result.impacts}
    assert "SystemTakeover" not in kinds


# FP-4a: broad network-ish names (remote/web/url) are not exfiltration channels


def test_fp4a_remote_and_web_search_no_exfiltration(tmp_path):
    caps = _parse_servers(tmp_path, {
        "remote": {"url": "https://mcp.example.com/sse"},
        "web-search": {
            "command": "npx",
            "args": ["-y", "web-search-mcp"],
        },
    })
    result = collect_impact(capabilities=caps, internet_reachable=True)

    kinds = {i.impact_kind for i in result.impacts}
    assert "Exfiltration" not in kinds


# FP-4b: "github" in a name is not evidence of git write capability


def test_fp4b_unknown_github_named_server_no_supply_chain(tmp_path):
    repo = _git_repo_with_remote(tmp_path)

    caps = _parse_servers(tmp_path, {
        "github-issues-reader": {
            "command": "npx",
            "args": ["-y", "github-issues-reader-mcp"],
        },
    })
    result = collect_impact(
        capabilities=caps, internet_reachable=True, source_repos=[str(repo)]
    )

    kinds = {i.impact_kind for i in result.impacts}
    assert "SupplyChainContamination" not in kinds


# Contrast case: the official github server really can push (push_files /
# create_or_update_file), so SupplyChain must still fire -- with the package
# identity as the reason, not a substring hit.


def test_known_github_package_still_triggers_supply_chain(tmp_path):
    repo = _git_repo_with_remote(tmp_path)

    caps = _parse_servers(tmp_path, {
        "github": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
        },
    })
    result = collect_impact(
        capabilities=caps, internet_reachable=True, source_repos=[str(repo)]
    )

    kinds = {i.impact_kind for i in result.impacts}
    assert "SupplyChainContamination" in kinds
