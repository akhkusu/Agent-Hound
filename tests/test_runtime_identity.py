import json
from subprocess import CompletedProcess

import pytest
from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.discovery import runtime_identity
from agenthound.discovery.runtime_identity import RuntimeIdentity, RuntimeIdentityError


def test_observe_claude_pid(monkeypatch):
    monkeypatch.setattr(runtime_identity.sys, "platform", "win32")
    monkeypatch.setattr(
        runtime_identity.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, '{"pid":21088,"name":"claude.exe","sid":"S-1-5-21-1-2-3-1104"}', ""),
    )
    observed = runtime_identity.observe_claude_pid(21088)
    assert observed.pid == 21088
    assert observed.owner_sid == "S-1-5-21-1-2-3-1104"
    assert observed.observed_at


def test_observe_sole_claude_process(monkeypatch):
    monkeypatch.setattr(runtime_identity.sys, "platform", "win32")
    def run(command, **kwargs):
        assert "Name = 'claude.exe'" in command[-1]
        assert "$processes.Count -ne 1" in command[-1]
        return CompletedProcess(command, 0, '{"pid":21088,"name":"claude.exe","sid":"S-1-5-21-1-2-3-1104"}', "")
    monkeypatch.setattr(runtime_identity.subprocess, "run", run)
    assert runtime_identity.observe_claude_pid().pid == 21088


@pytest.mark.parametrize("response", [
    CompletedProcess([], 2, "", "failure"),
    CompletedProcess([], 0, '{"pid":21089,"name":"claude.exe","sid":"S-1-5-21-1"}', ""),
    CompletedProcess([], 0, '{"pid":21088,"name":"other.exe","sid":"S-1-5-21-1"}', ""),
    CompletedProcess([], 0, '{"pid":21088,"name":"claude.exe","sid":"invalid"}', ""),
])
def test_observe_rejects_unverified_identity(monkeypatch, response):
    monkeypatch.setattr(runtime_identity.sys, "platform", "win32")
    monkeypatch.setattr(runtime_identity.subprocess, "run", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeIdentityError):
        runtime_identity.observe_claude_pid(21088)


def test_observe_rejects_non_windows():
    if runtime_identity.sys.platform != "win32":
        with pytest.raises(RuntimeIdentityError, match="native Windows"):
            runtime_identity.observe_claude_pid(21088)


def test_cli_persists_runtime_evidence(tmp_path, monkeypatch):
    config = tmp_path / ".claude" / "settings.json"
    config.parent.mkdir()
    config.write_text("{}")
    output = tmp_path / "graph.json"
    monkeypatch.setattr("agenthound.cli.observe_claude_pid", lambda pid: RuntimeIdentity(pid, "S-1-5-21-1-2-3-1104", "2026-10-05T00:00:00+00:00"))
    result = CliRunner().invoke(cli, ["-c", str(config), "-w", str(tmp_path), "--claude-pid", "21088", "-o", str(output)])
    assert result.exit_code == 0, result.output
    graph = json.loads(output.read_text())["graph"]
    agent = next(node for node in graph["nodes"] if "Agent" in node["kinds"])
    assert agent["properties"]["runtime_pid"] == 21088
    assert agent["properties"]["runtime_owner_sid"] == "S-1-5-21-1-2-3-1104"
    assert agent["properties"]["runtime_observed_at"] == "2026-10-05T00:00:00+00:00"
    assert "Win32_Process.GetOwnerSid" in agent["properties"]["runtime_identity_evidence"]
    assert not any(edge["kind"] in ("RunsAs", "RunsOn") for edge in graph["edges"])


def test_cli_does_not_attach_pid_to_other_agent(tmp_path, monkeypatch):
    config = tmp_path / "claude_desktop_config.json"
    config.write_text("{}")
    output = tmp_path / "graph.json"
    monkeypatch.setattr("agenthound.cli.observe_claude_pid", lambda pid: RuntimeIdentity(pid, "S-1-5-21-1-2-3-1104", "2026-10-05T00:00:00+00:00"))
    result = CliRunner().invoke(cli, ["-c", str(config), "-w", str(tmp_path), "--claude-pid", "21088", "-o", str(output)])
    assert result.exit_code != 0
    assert not output.exists()


def test_cli_auto_observes_without_pid(tmp_path, monkeypatch):
    config = tmp_path / ".claude" / "settings.json"
    config.parent.mkdir()
    config.write_text("{}")
    output = tmp_path / "graph.json"
    observed_pids = []
    def observe(pid):
        observed_pids.append(pid)
        return RuntimeIdentity(21088, "S-1-5-21-1-2-3-1104", "2026-10-05T00:00:00+00:00")
    monkeypatch.setattr("agenthound.cli.observe_claude_pid", observe)
    result = CliRunner().invoke(cli, ["-c", str(config), "-w", str(tmp_path), "--process-identity", "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert observed_pids == [None]
    agent = next(node for node in json.loads(output.read_text())["graph"]["nodes"] if "Agent" in node["kinds"])
    assert agent["properties"]["runtime_pid"] == 21088


def test_cli_rejects_conflicting_selection(tmp_path):
    config = tmp_path / ".claude" / "settings.json"
    config.parent.mkdir()
    config.write_text("{}")
    output = tmp_path / "graph.json"
    result = CliRunner().invoke(cli, ["-c", str(config), "-w", str(tmp_path), "--process-identity", "--claude-pid", "21088", "-o", str(output)])
    assert result.exit_code != 0
    assert not output.exists()
