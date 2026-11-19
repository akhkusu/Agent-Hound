import json

from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.discovery.copilot_windows import (
    copilot_inventory_graph,
    inventory_copilot_windows,
)


def profile(root, value, name="Default"):
    p = root / "Microsoft/Copilot/User Data" / name
    p.mkdir(parents=True)
    (p / "Preferences").write_text(json.dumps(value), encoding="utf-8")
    return p


def test_only_allowlisted_boolean_policies_are_exported(tmp_path):
    profile(
        tmp_path,
        {
            "copilot": {
                "ocps_policies": {
                    "isCopilotFileUploadEnabledWork": True,
                    "isCopilotWebSearchEnabledWork": "secret",
                    "token": "secret",
                }
            },
            "account_info": [{"email": "secret"}],
            "mcpServers": {"shell": "secret"},
        },
    )
    inv = inventory_copilot_windows(tmp_path)
    cached = inv["observations"][0]["profiles"][0]["cached_policy_booleans"]
    assert cached == {"isCopilotFileUploadEnabledWork": True}
    assert "secret" not in json.dumps(inv)
    graph = copilot_inventory_graph(inv)
    assert len(graph["graph"]["nodes"]) == 1
    assert graph["graph"]["edges"] == []


def test_missing_roots_and_environment(tmp_path, monkeypatch):
    assert inventory_copilot_windows(tmp_path)["observations"] == []
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    result = CliRunner().invoke(cli, ["copilot-windows"])
    assert result.exit_code != 0
    assert "LOCALAPPDATA" in result.output


def test_invalid_json_and_profiles_are_not_silently_treated_as_valid(tmp_path):
    p = profile(tmp_path, [], "Profile 2")
    profile(tmp_path, {}, "Default")
    profile(tmp_path, {}, "not-a-profile")
    inv = inventory_copilot_windows(tmp_path)
    assert len(inv["observations"][0]["profiles"]) == 2
    assert inv["observations"][0]["profiles"][1]["status"] == "invalid_object"
    (p / "Preferences").write_text("{bad", encoding="utf-8")
    assert (
        inventory_copilot_windows(tmp_path)["observations"][0]["profiles"][1]["status"]
        == "unreadable_or_invalid"
    )


def test_cli_writes_distinct_inventory_and_graph(tmp_path):
    profile(tmp_path, {})
    out = tmp_path / "inv.json"
    graph = tmp_path / "graph.json"
    args = ["copilot-windows", "--local-app-data", str(tmp_path), "-o", str(out)]
    result = CliRunner().invoke(cli, args + ["--graph-output", str(graph)])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["schema_version"] == 2
    assert json.loads(graph.read_text())["graph"]["edges"] == []
    result = CliRunner().invoke(cli, args + ["--graph-output", str(out)])
    assert result.exit_code != 0


def test_cli_cannot_overwrite_scanned_preferences(tmp_path):
    p = profile(tmp_path, {}) / "Preferences"
    original = p.read_bytes()
    result = CliRunner().invoke(
        cli, ["copilot-windows", "--local-app-data", str(tmp_path), "-o", str(p)]
    )
    assert result.exit_code != 0
    assert p.read_bytes() == original


def test_officehub_and_unreadable_settings_are_explicit(tmp_path, monkeypatch):
    from pathlib import Path

    root = (
        tmp_path
        / "Packages/Microsoft.MicrosoftOfficeHub_8wekyb3d8bbwe/LocalState/EBWebView"
    )
    p = root / "Default/Preferences"
    p.parent.mkdir(parents=True)
    p.write_text("{}", encoding="utf-8")
    original = Path.open

    def denied(path, *args, **kwargs):
        if path == p:
            raise PermissionError("Denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    observation = inventory_copilot_windows(tmp_path)["observations"][0]
    assert observation["product"] == "officehub-webview"
    assert observation["local_state_status"] == "missing"
    assert observation["profiles"][0]["status"] == "unreadable_or_invalid"


def test_settings_read_is_bounded(tmp_path, monkeypatch):
    from agenthound.discovery import copilot_windows

    profile(tmp_path, {"oversized": "value that exceeds the test limit"})
    monkeypatch.setattr(copilot_windows, "_MAX_SETTINGS_BYTES", 10)
    result = inventory_copilot_windows(tmp_path)
    assert result["observations"][0]["profiles"][0]["status"] == "too_large"


def test_auth_webview_and_store_presence_do_not_export_contents_or_create_agent(tmp_path):
    main = profile(tmp_path, {}).parent
    auth = main / "OneAuth/WebView2/EBWebView/Default"
    auth.mkdir(parents=True)
    (auth / "Preferences").write_text('{"token":"secret"}', encoding="utf-8")
    (auth / "Network").mkdir()
    (auth / "Network/Cookies").write_text("secret", encoding="utf-8")
    shared = tmp_path / "Microsoft/OneAuth/accounts"
    shared.mkdir(parents=True)
    (shared / "credential").write_text("secret", encoding="utf-8")
    inv = inventory_copilot_windows(tmp_path)
    assert len(inv["observations"]) == 2
    assert inv["observations"][1]["profiles"][0]["authentication_store_presence"]["cookies"]["status"] == "present"
    assert inv["shared_authentication_store_presence"]["oneauth_accounts"]["status"] == "present"
    assert inv["registry"]["status"] == "not_requested"
    assert "secret" not in json.dumps(inv)
    assert len(copilot_inventory_graph(inv)["graph"]["nodes"]) == 1


def test_shared_auth_store_is_protected_from_output(tmp_path):
    target = tmp_path / "Microsoft/OneAuth/blobs/token"
    target.parent.mkdir(parents=True)
    target.write_text("original", encoding="utf-8")
    result = CliRunner().invoke(
        cli, ["copilot-windows", "--local-app-data", str(tmp_path), "-o", str(target)]
    )
    assert result.exit_code != 0
    assert target.read_text() == "original"


def test_mcp_store_presence_does_not_read_definitions_or_create_access_edges(tmp_path, monkeypatch):
    from pathlib import Path

    root = tmp_path / "Microsoft/Copilot Dev/User Data/Autopilot/Mcp"
    account = root / "accounts/synthetic-partition"
    account.mkdir(parents=True)
    (account / "user-servers.json").write_text('{"token":"sentinel-secret"}')
    original_open = Path.open

    def deny_store_reads(path, *args, **kwargs):
        if path.is_relative_to(root):
            raise AssertionError("MCP definitions and credentials must not be opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_store_reads)
    inventory = inventory_copilot_windows(tmp_path)
    stores = {store["product"]: store for store in inventory["mcp_storage_presence"]}
    assert stores["Copilot Dev"]["status"] == "present"
    assert stores["Copilot Dev"]["accounts"]["status"] == "present"
    assert stores["Copilot"]["status"] == "missing"
    assert "sentinel-secret" not in json.dumps(inventory)
    assert copilot_inventory_graph(inventory)["graph"]["nodes"] == []
    assert copilot_inventory_graph(inventory)["graph"]["edges"] == []


def test_cli_cannot_overwrite_legacy_mcp_store(tmp_path):
    target = tmp_path / "Microsoft/Undocked/Autopilot/Mcp/accounts/partition/registry.json"
    target.parent.mkdir(parents=True)
    target.write_text("original")
    result = CliRunner().invoke(cli, [
        "copilot-windows", "--local-app-data", str(tmp_path), "-o", str(target),
    ])
    assert result.exit_code != 0
    assert target.read_text() == "original"


def test_file_read_settings_are_distinct_from_upload_and_runtime_access(tmp_path):
    profile(tmp_path, {
        "copilot": {"ocps_policies": {"isCopilotFileUploadEnabledWork": True}},
        "wcp": {"search_app_data": {
            "is_file_search_allowed": False, "is_file_read_allowed": True,
        }},
    })
    inv = inventory_copilot_windows(tmp_path)
    p = inv["observations"][0]["profiles"][0]
    assert p["local_file_permission_settings"] == {
        "is_file_search_allowed": "cached_denied", "is_file_read_allowed": "cached_allowed",
    }
    assert inv["file_access_assessment"]["local_file_read"] == "unverified"
    assert copilot_inventory_graph(inv)["graph"]["edges"] == []
    result = CliRunner().invoke(cli, [
        "copilot-windows", "--local-app-data", str(tmp_path), "-o", str(tmp_path / "inv.json"),
    ])
    assert result.exit_code == 0, result.output
    assert "read=cached_allowed" in result.output
    assert "unverified" in result.output


def test_missing_and_mistyped_file_permissions_do_not_invent_defaults(tmp_path):
    profile(tmp_path, {"wcp": {"search_app_data": {"is_file_read_allowed": "secret"}}})
    inv = inventory_copilot_windows(tmp_path)
    assert set(inv["observations"][0]["profiles"][0]["local_file_permission_settings"].values()) == {"unknown"}
    assert "secret" not in json.dumps(inv)
