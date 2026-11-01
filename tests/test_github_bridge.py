import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.collectors.assets import collect_assets
from agenthound.collectors.base import CollectionResult
from agenthound.collectors.sources import collect_sources
from agenthound.models.nodes import Agent
from agenthound.output.github_bridge import build_github_bridge
from agenthound.output.opengraph import build_opengraph


FIXTURE = Path(__file__).parent / "fixtures" / "githound_sample_minimal.json"
COMMIT = "581ba7f1403496187f0d6a61fb2cad3b0d33b8c5"


def _agent_graph(commit=COMMIT, clean=True, remote="github.com/spectertst/branchprotectiontest"):
    return {
        "metadata": {"source_kind": "AgentHound"},
        "graph": {
            "nodes": [
                {
                    "id": "asset-repo",
                    "kinds": ["Asset"],
                    "properties": {
                        "asset_kind": "SourceCode",
                        "path": "/srv/project",
                        "git_remote_repository": remote,
                        "git_upstream_branch": "main",
                        "git_head_commit": commit,
                    },
                },
                {
                    "id": "source-agents",
                    "kinds": ["Source"],
                    "properties": {
                        "source_kind": "AgentInstruction",
                        "path": "/srv/project/AGENTS.md",
                        "git_repository_asset_id": "asset-repo",
                        "git_relative_path": "AGENTS.md",
                        "git_committed": True,
                        "git_clean": clean,
                    },
                },
            ],
            "edges": [],
        },
    }


def _githound_graph():
    return json.loads(FIXTURE.read_text())


def _git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_sample_fixture_emits_clone_and_supplied_source():
    edges = build_github_bridge(_agent_graph(), [_githound_graph()])["graph"]["edges"]
    assert [edge["kind"] for edge in edges] == ["AH_CloneOf", "AH_SuppliesSource"]
    assert edges[0]["end"]["value"] == "R_kgDOMuFnXA"
    assert edges[1]["start"]["value"] == "REF_kwDOMuFnXK9yZWZzL2hlYWRzL21haW4"
    assert edges[0]["properties"]["traversable"] is False
    assert edges[1]["properties"]["traversable"] is True


@pytest.mark.parametrize("agent", [
    _agent_graph(commit="0" * 40),
    _agent_graph(clean=False),
])
def test_unverified_source_only_emits_clone(agent):
    edges = build_github_bridge(agent, [_githound_graph()])["graph"]["edges"]
    assert [edge["kind"] for edge in edges] == ["AH_CloneOf"]


def test_unmatched_repository_emits_no_edges():
    payload = build_github_bridge(
        _agent_graph(remote="github.com/example/other"), [_githound_graph()]
    )
    assert payload["graph"]["edges"] == []


def test_ambiguous_repository_is_rejected():
    graph = _githound_graph()
    duplicate = json.loads(json.dumps(graph["graph"]["nodes"][0]))
    duplicate["id"] = "different-id"
    graph["graph"]["nodes"].append(duplicate)
    with pytest.raises(ValueError, match="Ambiguous GH_Repository"):
        build_github_bridge(_agent_graph(), [graph])


def test_github_bridge_cli_writes_edge_only_payload(tmp_path):
    agent_path = tmp_path / "agent.json"
    output = tmp_path / "bridge.json"
    agent_path.write_text(json.dumps(_agent_graph()))
    result = CliRunner().invoke(
        cli,
        [
            "github-bridge",
            "--agent-graph",
            str(agent_path),
            "--githound-graph",
            str(FIXTURE),
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert payload["graph"]["nodes"] == []
    assert len(payload["graph"]["edges"]) == 2


def test_real_git_metadata_builds_both_edges(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "AGENTS.md").write_text("rules")
    _git(tmp_path, "add", "AGENTS.md")
    _git(tmp_path, "commit", "-m", "initial")
    _git(
        tmp_path,
        "remote",
        "add",
        "origin",
        "https://github.com/SpecterTst/branchprotectiontest.git",
    )
    _git(tmp_path, "config", "branch.main.remote", "origin")
    _git(tmp_path, "config", "branch.main.merge", "refs/heads/main")

    agent = Agent(name="codex", platform="Codex", config_path=str(tmp_path))
    source_result = collect_sources(agent, tmp_path, "workspace")
    asset_result = collect_assets([], tmp_path, "workspace")
    result = CollectionResult()
    result.sources = source_result.sources
    result.assets = asset_result.assets

    githound = _githound_graph()
    branch = next(
        node for node in githound["graph"]["nodes"] if "GH_Branch" in node["kinds"]
    )
    branch["properties"]["commit_hash"] = _git(tmp_path, "rev-parse", "HEAD")
    edges = build_github_bridge(build_opengraph(result), [githound])["graph"]["edges"]
    assert [edge["kind"] for edge in edges] == ["AH_CloneOf", "AH_SuppliesSource"]
