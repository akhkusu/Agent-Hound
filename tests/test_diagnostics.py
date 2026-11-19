import json
from importlib import metadata
import subprocess
import urllib.request

import pytest

from agenthound import diagnostics


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    # Never read this machine's actual agent configuration or depend on its extras.
    monkeypatch.setattr(diagnostics, "discover_config_files", lambda workspace: [])
    monkeypatch.setattr(diagnostics.metadata, "version", lambda package: "1.0")
    monkeypatch.setattr(diagnostics.sys, "platform", "linux")


def test_missing_configs_offer_action_without_error(tmp_path):
    findings = diagnostics.run_diagnostics(tmp_path)
    config = next(item for item in findings if item.check == "configuration")
    assert config.status == "warning"
    assert "--config FILE" in config.message
    assert not any(item.status == "error" for item in findings)


def test_mixed_configs_continue_without_exposing_values(tmp_path, monkeypatch):
    good = tmp_path / "cursor_mcp.json"
    good.write_text(json.dumps({"mcpServers": {
        "private-tool-name": {"command": "secret-launcher", "env": {"TOKEN": "secret-env-value"}}
    }}))
    malformed = tmp_path / "bad.json"
    malformed.write_text('{"token": secret-json-literal}')
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"mcpServers": {"private-tool-name": {"command": 42}}}))
    validation = tmp_path / "validation.json"
    validation.write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [
        {"type": "command", "command": {"token": "secret-validation-value"}}
    ]}]}}))
    monkeypatch.setattr(diagnostics, "discover_config_files", lambda workspace: [malformed, good, invalid, validation])

    findings = diagnostics.run_diagnostics(tmp_path)
    configs = [item for item in findings if item.check == "configuration"]
    assert [(item.path, item.status) for item in configs] == [
        (malformed, "error"), (good, "ok"), (invalid, "error"), (validation, "error")
    ]
    assert "Cursor" in configs[1].message
    report = repr(findings)
    for secret in ("secret-json-literal", "secret-env-value", "secret-launcher", "private-tool-name", "secret-validation-value"):
        assert secret not in report


def test_explicit_config_bypasses_discovery_and_is_offline(tmp_path, monkeypatch):
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": {"remote": {
        "url": "https://example.invalid/mcp", "headers": {"Authorization": "secret-token"}
    }, "local": {"command": "must-not-start"}}}))

    def forbidden(*args, **kwargs):
        pytest.fail("Offline diagnostics attempted discovery or live activity")

    monkeypatch.setattr(diagnostics, "discover_config_files", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    before = set(tmp_path.iterdir())
    findings = diagnostics.run_diagnostics(tmp_path, config)
    assert next(item for item in findings if item.check == "configuration").status == "ok"
    assert set(tmp_path.iterdir()) == before
    assert "secret-token" not in repr(findings)


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_workspace_is_error_and_skips_parsing(tmp_path, monkeypatch, kind):
    workspace = tmp_path / kind
    if kind == "file":
        workspace.write_text("not a directory")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid workspace should not reach configuration discovery")

    monkeypatch.setattr(diagnostics, "discover_config_files", forbidden)
    findings = diagnostics.run_diagnostics(workspace)
    assert findings[0].check == "workspace"
    assert findings[0].status == "error"


def test_missing_explicit_config_is_safe_error(tmp_path):
    findings = diagnostics.run_diagnostics(tmp_path, tmp_path / "missing.json")
    assert next(item for item in findings if item.check == "configuration").status == "error"


@pytest.mark.parametrize("error", [metadata.PackageNotFoundError("mcp"), OSError("secret-detail")])
def test_optional_package_problems_are_warnings(tmp_path, monkeypatch, error):
    def unavailable(package):
        raise error

    monkeypatch.setattr(diagnostics.metadata, "version", unavailable)
    finding = next(item for item in diagnostics.run_diagnostics(tmp_path) if item.check == "mcp")
    assert finding.status == "warning"
    assert "agenthound[mcp]" in finding.message
    assert "secret-detail" not in finding.message


def test_windows_auth_metadata_is_only_checked_on_windows(tmp_path, monkeypatch):
    checked = []

    def version(package):
        checked.append(package)
        if package == "winrt-Windows.System":
            raise metadata.PackageNotFoundError(package)
        return "1.0"

    monkeypatch.setattr(diagnostics.metadata, "version", version)
    linux = diagnostics.run_diagnostics(tmp_path)
    assert checked == ["mcp"]
    assert not any(item.check == "windows-auth" for item in linux)
    monkeypatch.setattr(diagnostics.sys, "platform", "win32")
    windows = diagnostics.run_diagnostics(tmp_path)
    auth = next(item for item in windows if item.check == "windows-auth")
    assert auth.status == "warning"
    assert "agenthound[windows-auth]" in auth.message
