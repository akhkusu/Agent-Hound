import json

import pytest
from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.discovery import copilot_files as auto

TENANT = "11111111-1111-4111-8111-111111111111"
ORIGIN = "https://example.sharepoint.com"
SITE = ORIGIN + "/sites/team"


def configure(monkeypatch):
    monkeypatch.setattr(auto, "discover_copilot_context", lambda: {
        "tenant_id": TENANT, "sharepoint_origin_candidates": [ORIGIN]})
    monkeypatch.setattr(auto, "discover_sharepoint_resource", lambda *args: "resource-guid")
    monkeypatch.setattr(auto, "acquire_copilot_sharepoint_token", lambda *args, **kwargs: "memory-only")
    monkeypatch.setattr(auto, "_sites", lambda *args: {SITE})

    class Reader:
        def __init__(self, *args):
            pass

        def get(self, url):
            return {"value": [{"ItemCount": 999, "RootFolder": {
                "ServerRelativeUrl": "/sites/team/Shared Documents"}}]}

    monkeypatch.setattr(auto, "SharePointReader", Reader)
    rows = [{"name": f"document-{index}.docx", "kind": "file", "size": 1,
             "last_modified": "2026-11-01T10:00:00Z", "web_url": SITE + f"/document-{index}.docx"}
            for index in range(25)]
    monkeypatch.setattr(auto, "collect_sharepoint_files", lambda **kwargs: {"items": rows, "complete": True})


def test_default_returns_twenty_without_claiming_total_or_content_access(monkeypatch):
    configure(monkeypatch)
    result = auto.collect_copilot_files()
    assert len(result["items"]) == 20
    assert result["enumerated_file_count"] == 20
    assert result["total_files_in_discovered_libraries"] is None
    assert result["content_access_verified_count"] is None
    assert result["libraries"][0]["reported_item_count"] == 999
    assert result["stopped_early"] is True
    assert result["enumeration_complete"] is False
    assert result["site_discovery_complete"] is False
    assert "memory-only" not in json.dumps(result)


def test_full_enumeration_counts_metadata_separately_and_still_displays_twenty(monkeypatch):
    configure(monkeypatch)
    result = auto.collect_copilot_files(enumerate_all=True)
    assert len(result["items"]) == 20
    assert result["total_files_in_discovered_libraries"] == 25
    assert result["enumeration_complete"] is True
    assert result["complete"] is False  # Search-index coverage is not tenant coverage.
    assert result["content_access_verified_count"] is None


def test_no_argument_cli_resolves_context_and_defaults_to_twenty(monkeypatch, tmp_path):
    configure(monkeypatch)
    output = tmp_path / "files.json"
    result = CliRunner().invoke(cli, ["m365-files", "--json", "-o", str(output)])
    assert result.exit_code == 0, result.output
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["display_limit"] == 20
    assert saved["tenant_id"] == TENANT
    assert len(saved["items"]) == 20


def test_listing_failure_is_preserved_and_never_becomes_a_zero_total(monkeypatch):
    configure(monkeypatch)

    def denied(**kwargs):
        raise auto.M365FilesError("SharePoint request failed (HTTP 403)")

    monkeypatch.setattr(auto, "collect_sharepoint_files", denied)
    result = auto.collect_copilot_files(enumerate_all=True)
    assert result["enumerated_file_count"] == 0
    assert result["total_files_in_discovered_libraries"] is None
    assert result["enumeration_complete"] is False
    assert result["errors"][0]["stage"] == "folder_listing"


def test_verified_host_preserves_authentication_denial_without_trying_other_hosts(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(auto, "discover_copilot_context", lambda: {
        "tenant_id": TENANT, "sharepoint_origin_candidates": [ORIGIN, "https://other.sharepoint.com"]})
    verified = []

    def verify(origin, tenant):
        verified.append(origin)
        return "resource-guid"

    def deny(*args, **kwargs):
        raise auto.M365FilesError("Silent authentication denied (AADSTS65002)")

    monkeypatch.setattr(auto, "discover_sharepoint_resource", verify)
    monkeypatch.setattr(auto, "acquire_copilot_sharepoint_token", deny)
    with pytest.raises(auto.M365FilesError, match="AADSTS65002"):
        auto.collect_copilot_files()
    assert verified == [ORIGIN]
