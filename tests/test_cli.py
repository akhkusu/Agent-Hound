import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from agenthound.cli import cli

FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_without_command_shows_help():
    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_scan_with_config(tmp_path):
    runner = CliRunner()
    out = tmp_path / "out.json"
    result = runner.invoke(cli, [
        "scan",
        "--config", str(FIXTURES / "claude_desktop_config.json"),
        "--workspace", str(tmp_path),
        "--scope", "workspace",
        "--output", str(out),
    ])
    assert result.exit_code == 0
    assert out.exists()
    data = json.loads(out.read_text())
    assert "graph" in data


def test_scan_verbose_shows_scope_summary(tmp_path):
    runner = CliRunner()
    out = tmp_path / "out.json"
    result = runner.invoke(cli, [
        "scan",
        "--config", str(FIXTURES / "claude_desktop_config.json"),
        "--workspace", str(tmp_path),
        "--scope", "workspace",
        "--output", str(out),
        "--verbose",
    ])
    assert result.exit_code == 0
    assert "Scope" in result.output or "Capabilities" in result.output


def test_scan_output_contains_agent_node(tmp_path):
    runner = CliRunner()
    out = tmp_path / "out.json"
    runner.invoke(cli, [
        "scan",
        "--config", str(FIXTURES / "claude_desktop_config.json"),
        "--workspace", str(tmp_path),
        "--scope", "workspace",
        "--output", str(out),
    ])
    data = json.loads(out.read_text())
    kinds_in_graph = [k for node in data["graph"]["nodes"] for k in node["kinds"]]
    assert "Agent" in kinds_in_graph


def test_scan_excludes_general_documents(tmp_path):
    config = FIXTURES / "claude_desktop_config.json"
    (tmp_path / "README.md").write_text("# project", encoding="utf-8")
    default_output = tmp_path / "default.json"
    default_result = CliRunner().invoke(cli, [
        "scan", "--config", str(config), "--workspace", str(tmp_path),
        "--scope", "workspace", "--output", str(default_output),
    ])
    assert default_result.exit_code == 0, default_result.output
    default_nodes = json.loads(default_output.read_text())["graph"]["nodes"]
    assert not any(node["properties"].get("source_kind") == "DocFile" for node in default_nodes)


def test_scan_subcommand_runs(tmp_path, monkeypatch):
    # Keep the test independent of real user configuration files. Parsing
    # malformed discovered configs is covered separately from CLI dispatch.
    monkeypatch.setattr("agenthound.cli.discover_config_files", lambda workspace=None: [])
    runner = CliRunner()
    out = tmp_path / "out.json"
    result = runner.invoke(cli, ["scan", "--output", str(out), "--scope", "workspace",
                                  "--workspace", str(tmp_path)])
    assert result.exit_code == 0


def test_help_focuses_on_main_commands_and_reveals_advanced_features():
    runner = CliRunner()
    basic = runner.invoke(cli, ["--help"])
    detailed = runner.invoke(cli, ["--help-all"])
    assert basic.exit_code == detailed.exit_code == 0
    for command in ("scan", "doctor"):
        assert command in basic.output
    for command in ("ad-bridge", "github-bridge", "m365-files", "register-icons"):
        assert command not in basic.output
        assert command in detailed.output
    scan_help = runner.invoke(cli, ["scan", "--help"])
    scan_details = runner.invoke(cli, ["scan", "--help-all"])
    assert scan_help.exit_code == scan_details.exit_code == 0
    assert "--output" in scan_help.output
    assert "--help-all" in scan_help.output
    assert "--copilot-client-id" not in scan_help.output
    assert "--copilot-client-id" in scan_details.output


@pytest.mark.parametrize("arguments", [["discover"], ["-o", "ignored.json", "scan"]])
def test_removed_command_and_misplaced_options_are_errors(arguments, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid command must not start a scan")

    monkeypatch.setattr("agenthound.cli._run_scan", forbidden)
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 2
    assert "No such" in result.output


def test_positional_scan_target_reaches_collection(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    discovered = []
    scanned = []

    def configs(workspace):
        discovered.append(workspace)
        return []

    monkeypatch.setattr("agenthound.cli.discover_config_files", configs)
    monkeypatch.setattr("agenthound.cli._run_scan", lambda *args, **kwargs: scanned.append((args, kwargs)))
    result = CliRunner().invoke(cli, ["scan", str(project), "--no-copilot", "-o", "wanted.json"])
    assert result.exit_code == 0, result.output
    assert discovered == [project]
    assert scanned[0][0][1] == project
    assert scanned[0][0][3] == Path("wanted.json")
    assert scanned[0][1]["merge_claude"] is True


def test_scan_rejects_ambiguous_project_selection(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Ambiguous scan must not discover or collect")

    monkeypatch.setattr("agenthound.cli.discover_config_files", forbidden)
    monkeypatch.setattr("agenthound.cli._run_scan", forbidden)
    result = CliRunner().invoke(cli, ["scan", str(tmp_path), "-w", str(tmp_path)])
    assert result.exit_code == 2
    assert "Use PATH or --workspace" in result.output


def test_single_config_scan_does_not_discover_or_add_automatic_inventory(tmp_path, monkeypatch):
    config = FIXTURES / "claude_desktop_config.json"
    scanned = []

    def forbidden(*args, **kwargs):
        pytest.fail("Explicit config must not discover other configurations or inventory")

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("agenthound.cli.discover_config_files", forbidden)
    monkeypatch.setattr("agenthound.discovery.copilot_windows.inventory_copilot_windows", forbidden)
    monkeypatch.setattr("agenthound.cli._run_scan", lambda *args, **kwargs: scanned.append((args, kwargs)))
    result = CliRunner().invoke(cli, ["scan", str(tmp_path), "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert scanned[0][0][0] == [config]
    assert scanned[0][1]["merge_claude"] is False
    assert scanned[0][1]["extra_graphs"] == []


def test_single_config_scan_keeps_explicit_cloud_collection(tmp_path, monkeypatch):
    config = FIXTURES / "claude_desktop_config.json"
    cloud_graph = {"graph": {"nodes": [], "edges": []}}
    scanned = []
    monkeypatch.setattr("agenthound.discovery.copilot_files.collect_copilot_files", lambda **kwargs: {"items": []})
    monkeypatch.setattr("agenthound.output.unified.build_metadata_graph", lambda listing: cloud_graph)
    monkeypatch.setattr("agenthound.cli._run_scan", lambda *args, **kwargs: scanned.append((args, kwargs)))
    result = CliRunner().invoke(cli, ["scan", str(tmp_path), "-c", str(config), "--m365"])
    assert result.exit_code == 0, result.output
    assert scanned[0][1]["extra_graphs"] == [cloud_graph]


def test_doctor_reports_safe_errors_without_creating_graph(tmp_path, monkeypatch):
    from agenthound import diagnostics

    config = tmp_path / "claude_desktop_config.json"
    config.write_text('{"token": "private-token", "mcpServers": []', encoding="utf-8")
    monkeypatch.setattr(diagnostics.metadata, "version", lambda package: "2.3")
    before = set(tmp_path.iterdir())
    result = CliRunner().invoke(cli, ["doctor", str(tmp_path), "--config", str(config)])
    assert result.exit_code == 1
    assert "ERROR" in result.output
    assert "Configuration could not be parsed" in result.output
    assert "private-token" not in result.output
    assert "authentication were not tested" in result.output
    assert set(tmp_path.iterdir()) == before


def test_doctor_missing_optional_dependencies_are_actionable_warnings(tmp_path, monkeypatch):
    from agenthound import diagnostics

    def missing(package):
        raise diagnostics.metadata.PackageNotFoundError(package)

    monkeypatch.setattr(diagnostics, "discover_config_files", lambda workspace: [])
    monkeypatch.setattr(diagnostics.metadata, "version", missing)
    result = CliRunner().invoke(cli, ["doctor", str(tmp_path)])
    assert result.exit_code == 0
    assert "WARNING" in result.output
    assert "--config FILE" in result.output
    assert "agenthound[mcp]" in result.output
