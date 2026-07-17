import json
from pathlib import Path
from click.testing import CliRunner
from agenthound.cli import cli

FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_requires_config_or_subcommand():
    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_scan_with_config(tmp_path):
    runner = CliRunner()
    out = tmp_path / "out.json"
    result = runner.invoke(cli, [
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
        "--config", str(FIXTURES / "claude_desktop_config.json"),
        "--workspace", str(tmp_path),
        "--scope", "workspace",
        "--output", str(out),
    ])
    data = json.loads(out.read_text())
    kinds_in_graph = [k for node in data["graph"]["nodes"] for k in node["kinds"]]
    assert "Agent" in kinds_in_graph


def test_discover_subcommand_runs(tmp_path, monkeypatch):
    # Keep the test independent of real user configuration files. Parsing
    # malformed discovered configs is covered separately from CLI dispatch.
    monkeypatch.setattr("agenthound.cli.discover_config_files", lambda: [])
    runner = CliRunner()
    out = tmp_path / "out.json"
    result = runner.invoke(cli, ["discover", "--output", str(out), "--scope", "workspace",
                                  "--workspace", str(tmp_path)])
    assert result.exit_code == 0
