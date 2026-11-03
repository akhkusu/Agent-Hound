import copy
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.discovery.m365_snapshot import M365Snapshot
from agenthound.output.attack_paths import attack_paths
from agenthound.output.m365 import build_m365_graph
from agenthound.output.unified import build_metadata_graph, merge_graphs


def listing():
    return {"tenant_id": "test-tenant", "observed_at": "2026-11-01T09:00:00Z",
            "authentication": "copilot_wam", "items": [
                {"kind": "file", "name": "計画.docx", "web_url": "https://example.sharepoint.com/plan.docx"},
                {"kind": "folder", "name": "Folder"}], "complete": False,
            "enumerated_file_count": 1, "errors": []}


def snapshot():
    path = Path(__file__).resolve().parents[1] / "examples/m365_snapshot.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_metadata_scope_does_not_claim_document_retrieval():
    payload = build_metadata_graph(listing())
    graph = payload["graph"]
    assert {e["kind"] for e in graph["edges"]} == {"HasCapability", "CanAccess", "Influences"}
    assert any(n["kinds"] == ["Source", "Asset"] for n in graph["nodes"])
    path = next(attack_paths(payload))
    assert path["target"] == "計画.docx"
    assert path["context"] == "explicit document context required"
    assert path["confidence"] == "suspected"
    edge = graph["edges"][-1]
    assert edge["properties"]["retrieval_verified"] is False
    assert edge["properties"]["access_scope"] == "document_content"
    assert edge["properties"]["metadata_access_verified"] is True
    assert all(n["properties"]["content_access_verification"] == "not_tested" for n in graph["nodes"])
    assert payload["metadata"] == {"source_kind": "AgentHound"}
    assert graph["nodes"][0]["properties"]["collection_complete"] is False
    assert graph["nodes"][0]["properties"]["retained_file_count"] == 1


def test_metadata_graph_rejects_wrong_auth_and_unsafe_urls():
    data = listing()
    data["authentication"] = "external_token"
    with pytest.raises(ValueError):
        build_metadata_graph(data)
    data = listing()
    data["items"][0]["web_url"] += "?token=sentinel"
    with pytest.raises(ValueError):
        build_metadata_graph(data)


def test_merge_does_not_join_local_and_cloud_agents_by_name():
    cloud = build_m365_graph(M365Snapshot.model_validate(snapshot()))
    local = {"graph": {"nodes": [{"id": "local-copilot", "kinds": ["Agent"],
             "properties": copy.deepcopy(next(n["properties"] for n in cloud["graph"]["nodes"]
                                               if "Agent" in n["kinds"]))}], "edges": []}}
    merged = merge_graphs(local, cloud)
    assert merged["metadata"] == cloud["metadata"] == {"source_kind": "AgentHound"}
    assert len([n for n in merged["graph"]["nodes"] if "Agent" in n["kinds"]]) == 2
    assert all(e["start"]["value"] != "local-copilot" for e in merged["graph"]["edges"])
    conflicting = copy.deepcopy(local)
    conflicting["graph"]["nodes"][0]["properties"]["name"] = "conflict"
    with pytest.raises(ValueError, match="Conflicting"):
        merge_graphs(local, conflicting)


def test_snapshot_paths_preserve_context_and_weakest_confidence():
    data = snapshot()
    agent = data["agents"][0]
    agent["references"] = [{"document_id": "orion-pricing", "mode": "retrieved",
                            "evidence": "Known content verified", "retrieval_route": "explicit_document_context"}]
    paths = list(attack_paths(build_m365_graph(M365Snapshot.model_validate(data))))
    retrieved = next(p for p in paths if p["target"].endswith("価格.xlsx"))
    assert retrieved["confidence"] == "confirmed"
    assert retrieved["context"] == "explicit document context required"
    assert any(p["confidence"] == "suspected" for p in paths)


def test_discover_combines_copilot_and_snapshot_without_local_configs(tmp_path, monkeypatch):
    monkeypatch.setattr("agenthound.cli.discover_config_files", lambda workspace: [])
    (tmp_path / "Microsoft/Copilot/User Data").mkdir(parents=True)
    source = tmp_path / "snapshot.json"
    source.write_text(json.dumps(snapshot()), encoding="utf-8")
    out = tmp_path / "combined.json"
    result = CliRunner().invoke(cli, ["discover", "-w", str(tmp_path), "--scope", "workspace",
        "--local-app-data", str(tmp_path), "--m365-snapshot", str(source), "-o", str(out)])
    assert result.exit_code == 0, result.output
    graph = json.loads(out.read_text(encoding="utf-8"))["graph"]
    assert len([n for n in graph["nodes"] if "Agent" in n["kinds"]]) == 2
    assert "Attack Paths" in result.output
    assert any(n["properties"].get("name") == "Project_ORION_顧客別価格.xlsx" for n in graph["nodes"])
    assert {e["kind"] for e in graph["edges"]} <= {"HasCapability", "CanAccess", "Influences"}


def test_discover_live_collection_uses_same_graph(tmp_path, monkeypatch):
    monkeypatch.setattr("agenthound.cli.discover_config_files", lambda workspace: [])
    calls = []

    def collect(**kwargs):
        calls.append(kwargs)
        return listing()

    monkeypatch.setattr("agenthound.discovery.copilot_files.collect_copilot_files", collect)
    out = tmp_path / "live.json"
    result = CliRunner().invoke(cli, ["discover", "-w", str(tmp_path), "--no-copilot",
                                      "--m365", "--limit", "5", "--enumerate-all", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert calls[0]["display_limit"] == 5
    assert calls[0]["enumerate_all"] is True
    assert "content access unverified" in result.output
    assert next(attack_paths(json.loads(out.read_text(encoding="utf-8"))))["target"] == "計画.docx"


@pytest.mark.parametrize("args", [["--no-copilot", "--include-registry"],
                                   ["--site-url", "https://example.sharepoint.com"], ["--limit", "5"],
                                   ["--enumerate-all"]])
def test_discover_rejects_unused_options(args):
    result = CliRunner().invoke(cli, ["discover", *args])
    assert result.exit_code != 0


def test_discover_protects_snapshot_and_copilot_stores(tmp_path, monkeypatch):
    monkeypatch.setattr("agenthound.cli.discover_config_files", lambda workspace: [])
    source = tmp_path / "snapshot.json"
    source.write_text(json.dumps(snapshot()), encoding="utf-8")
    args = ["discover", "--local-app-data", str(tmp_path), "--m365-snapshot", str(source), "-o"]
    assert CliRunner().invoke(cli, args + [str(source)]).exit_code != 0
    target = tmp_path / "Microsoft/OneAuth/accounts/account.json"
    target.parent.mkdir(parents=True)
    target.write_text("original", encoding="utf-8")
    assert CliRunner().invoke(cli, args + [str(target)]).exit_code != 0
    assert target.read_text(encoding="utf-8") == "original"


def test_discover_no_copilot_does_not_collect_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr("agenthound.cli.discover_config_files", lambda workspace: [])

    def unexpected(*args, **kwargs):
        raise AssertionError("Inventory must be skipped")

    monkeypatch.setattr("agenthound.discovery.copilot_windows.inventory_copilot_windows", unexpected)
    result = CliRunner().invoke(cli, ["discover", "--no-copilot", "-w", str(tmp_path), "-o", str(tmp_path / "empty.json")])
    assert result.exit_code == 0, result.output
    assert "no capability-to-target paths" in result.output


def test_local_lab_is_excluded_from_source_and_asset_discovery(tmp_path):
    from agenthound.discovery.workspace import find_asset_files, find_source_files

    lab = tmp_path / ".local/lab"
    lab.mkdir(parents=True)
    (lab / "AGENTS.md").write_text("private lab instruction", encoding="utf-8")
    (lab / ".env").write_text("SYNTHETIC=placeholder", encoding="utf-8")
    (tmp_path / "README.md").write_text("public", encoding="utf-8")
    assert len(find_source_files(tmp_path)) == 1
    assert find_asset_files("workspace", tmp_path) == []


def test_discovery_reads_utf8_bom_configs_with_document_names(tmp_path):
    from agenthound.discovery.claude_mcp import load_object
    from agenthound.discovery.claude_settings import collect_claude_settings
    from agenthound.discovery.config_parser import parse_config

    data = {"description": "日本語の文書名📄", "permissions": {"allow": ["Read"]}}
    config = tmp_path / ".claude/settings.json"
    config.parent.mkdir()
    config.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")
    assert load_object(config) == data
    _, caps = collect_claude_settings([config], tmp_path)
    read = next(c for c in caps if c.name == "Read")
    assert read.read_policy_sources[0]["permissions"]["allow"] == ["Read"]
    assert parse_config(config)[0].name == "claude-code"


def test_sharepoint_candidates_form_original_attack_chain_and_omit_system_files():
    data = listing()
    doc = data["items"][0]
    doc.update(site_url="https://example.sharepoint.com/sites/team", library="Shared Documents",
               library_title="Documents", web_url="https://example.sharepoint.com/sites/team/Shared%20Documents/plan.docx")
    data["items"].extend([
        {**doc, "library": "_catalogs/masterpage", "web_url": "https://example.sharepoint.com/sites/team/_catalogs/masterpage/CustomStrings.js"},
        {**doc, "web_url": "https://example.sharepoint.com/sites/team/Shared%20Documents/Forms/EditForm.aspx"},
    ])
    graph = build_metadata_graph(data)["graph"]
    asset = next(n for n in graph["nodes"] if "Asset" in n["kinds"])
    assert len(graph["nodes"]) == 3
    assert asset["kinds"] == ["Source", "Asset"]
    source_edge = next(e for e in graph["edges"] if e["kind"] == "Influences")
    capability = next(e for e in graph["edges"] if e["kind"] == "HasCapability")
    access = next(e for e in graph["edges"] if e["kind"] == "CanAccess")
    assert source_edge["start"]["value"] == asset["id"]
    assert source_edge["end"] == capability["start"]
    assert capability["end"] == access["start"]
    assert access["end"]["value"] == asset["id"]
    assert all(e["properties"]["confidence"] == "suspected" for e in graph["edges"])


def test_unified_full_attack_chain_covers_claude_codex_and_copilot(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    home.mkdir()
    (workspace / ".claude").mkdir(parents=True)
    (workspace / ".codex").mkdir()
    claude = workspace / ".claude/settings.json"
    codex = workspace / ".codex/config.toml"
    claude.write_text('{"permissions":{"allow":["Read"],"deny":["Bash"]}}', encoding="utf-8")
    codex.write_text("", encoding="utf-8")
    (workspace / "README.md").write_text("synthetic source", encoding="utf-8")
    (workspace / ".env").write_text("SYNTHETIC=placeholder", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr("agenthound.cli.discover_config_files", lambda workspace: [claude, codex])
    monkeypatch.setattr("agenthound.cli.check_internet", lambda: False)
    monkeypatch.setattr("agenthound.discovery.copilot_files.collect_copilot_files", lambda **kwargs: listing())
    out = workspace / "graph.json"
    result = CliRunner().invoke(cli, ["discover", "--no-copilot", "--m365", "-w", str(workspace),
                                      "--scope", "workspace", "-o", str(out)])
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    paths = list(attack_paths(payload))
    assert {p["agent"] for p in paths} >= {"Claude Code", "Codex", "Copilot (SharePoint work-account context)"}
    assert all(p["entry"].endswith("(Influences)") for p in paths)
