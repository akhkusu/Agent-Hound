import base64
import io
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest
from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.discovery import m365_files as files

TENANT = "11111111-1111-4111-8111-111111111111"
SITE = "https://example.sharepoint.com/sites/allcompany"


def token(tenant=TENANT, audience="00000003-0000-0000-c000-000000000000"):
    body = base64.urlsafe_b64encode(json.dumps({"tid": tenant, "aud": audience}).encode()).decode().rstrip("=")
    return "header." + body + ".signature-secret"


def child(identity="file-1", name="Overview.docx"):
    return {"id": identity, "name": name, "size": 100, "lastModifiedDateTime": "2026-11-01T09:00:00Z",
            "webUrl": SITE + "/Shared%20Documents/Project_ORION/" + name,
            "file": {}, "parentReference": {"driveId": "drive-1", "id": "folder-1"},
            "@microsoft.graph.downloadUrl": "https://secret.invalid/download?token=secret"}


def setup_reader(monkeypatch, responses):
    urls = []

    class Reader:
        def __init__(self, bearer):
            assert bearer == token()

        def get(self, url):
            urls.append(url)
            return responses[len(urls) - 1]

    monkeypatch.setattr(files, "GraphReader", Reader)
    return urls


def folder():
    return {"id": "folder-1", "name": "Project_ORION", "folder": {},
            "webUrl": SITE + "/Shared%20Documents/Project_ORION"}


def collect(**kwargs):
    return files.collect_m365_files(tenant_id=TENANT, token_provider=lambda _: token(), **kwargs)


def test_site_resolution_folder_encoding_and_paginated_live_listing(monkeypatch):
    continuation = files.GRAPH + "/drives/drive-1/items/folder-1/children?$skiptoken=opaque"
    urls = setup_reader(monkeypatch, [
        {"id": "site-1", "webUrl": SITE}, {"id": "drive-1"}, folder(),
        {"value": [child()], "@odata.nextLink": continuation},
        {"value": [dict(child("folder-2", "Subfolder"), folder={}, file=None)]},
    ])
    result = collect(site_url=SITE, folder="Project_ORION/日本語 folder")
    assert "/sites/example.sharepoint.com:/sites/allcompany?" in urls[0]
    assert "/root:/Project_ORION/%E6%97%A5%E6%9C%AC%E8%AA%9E%20folder?" in urls[2]
    assert urls[-1] == continuation
    assert result["complete"] is True and result["page_count"] == 2
    assert [item["kind"] for item in result["items"]] == ["file", "folder"]
    assert result["items"][0]["drive_id"] == "drive-1"
    assert "signature-secret" not in json.dumps(result)
    assert "downloadUrl" not in json.dumps(result)


@pytest.mark.parametrize("next_link", [
    "https://attacker.invalid/v1.0/drives/drive-1/items/folder-1/children",
    "http://graph.microsoft.com/v1.0/drives/drive-1/items/folder-1/children",
    "https://graph.microsoft.com/v1.0/me/messages",
    "https://graph.microsoft.com:443/v1.0/drives/drive-1/items/folder-1/children",
])
def test_pagination_never_leaves_graph_or_the_requested_folder(monkeypatch, next_link):
    urls = setup_reader(monkeypatch, [folder(), {"value": [], "@odata.nextLink": next_link}])
    with pytest.raises(files.M365FilesError, match="unexpected Graph URL"):
        collect(drive_id="drive-1", folder="Project_ORION")
    assert len(urls) == 2


def test_collection_limits_fail_instead_of_claiming_complete(monkeypatch):
    setup_reader(monkeypatch, [folder(), {"value": [child(), child("file-2")]}])
    with pytest.raises(files.M365FilesError, match="max-items"):
        collect(drive_id="drive-1", max_items=1)
    next_link = files.GRAPH + "/drives/drive-1/items/folder-1/children?$skiptoken=x"
    setup_reader(monkeypatch, [folder(), {"value": [], "@odata.nextLink": next_link}])
    with pytest.raises(files.M365FilesError, match="page limit"):
        collect(drive_id="drive-1", max_pages=1)


def test_office_document_web_url_is_supported_without_exporting_download_auth(monkeypatch):
    item = child()
    item["webUrl"] = SITE + "/_layouts/15/Doc.aspx?sourcedoc=%7B11111111-1111-4111-8111-111111111111%7D&file=Overview.docx&action=default&mobileredirect=true"
    setup_reader(monkeypatch, [folder(), {"value": [item]}])
    result = collect(drive_id="drive-1")
    assert result["items"][0]["web_url"] == item["webUrl"]
    assert "downloadUrl" not in json.dumps(result)


def test_document_web_url_with_auth_query_is_rejected_without_echo(monkeypatch):
    item = child()
    item["webUrl"] = SITE + "/_layouts/15/Doc.aspx?sourcedoc=%7B11111111-1111-4111-8111-111111111111%7D&authkey=secret"
    setup_reader(monkeypatch, [folder(), {"value": [item]}])
    with pytest.raises(files.M365FilesError) as error:
        collect(drive_id="drive-1")
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("kwargs", [
    {"site_url": "https://example.sharepoint.com/sites/allcompany?token=secret"},
    {"site_url": "https://evil.invalid/sites/site"},
    {"drive_id": "drive-1", "folder": "../outside"},
    {"drive_id": "drive-1", "folder": "https://evil.invalid"},
    {"drive_id": "drive-1", "folder": "a//b"},
])
def test_bad_scope_is_rejected_before_authentication(monkeypatch, kwargs):
    def unexpected(*args):
        pytest.fail("Authentication must not run for invalid input")

    monkeypatch.setattr(files, "acquire_graph_token", unexpected)
    with pytest.raises(files.M365FilesError):
        files.collect_m365_files(tenant_id=TENANT, **kwargs)


def test_cross_folder_child_and_duplicate_ids_are_not_silently_accepted(monkeypatch):
    item = child()
    item["parentReference"]["id"] = "other-folder"
    setup_reader(monkeypatch, [folder(), {"value": [item]}])
    with pytest.raises(files.M365FilesError, match="outside"):
        collect(drive_id="drive-1")
    setup_reader(monkeypatch, [folder(), {"value": [child(), child()]}])
    with pytest.raises(files.M365FilesError, match="duplicate"):
        collect(drive_id="drive-1")


def test_explicit_token_is_tenant_and_audience_guarded_without_azure_cli(monkeypatch):
    monkeypatch.setenv("GRAPH_TOKEN", token())
    assert files.acquire_graph_token(TENANT, "GRAPH_TOKEN") == token()
    monkeypatch.setenv("GRAPH_TOKEN", token(tenant="other-tenant"))
    with pytest.raises(files.M365FilesError, match="requested tenant") as error:
        files.acquire_graph_token(TENANT, "GRAPH_TOKEN")
    assert "signature-secret" not in str(error.value)
    with pytest.raises(files.M365FilesError, match="Choose --copilot-wam"):
        files.acquire_graph_token(TENANT)


@pytest.mark.parametrize("status, message", [(401, "authentication failed"), (403, "access denied"),
                                            (404, "not found"), (429, "throttled"), (302, "redirect rejected")])
def test_http_errors_never_echo_auth_or_api_error_bodies(monkeypatch, status, message):
    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == "GET"
            assert request.headers["Authorization"] == "Bearer credential-secret"
            raise HTTPError(request.full_url, status, "raw credential-secret", {}, io.BytesIO(b"credential-secret"))

    reader = files.GraphReader("credential-secret")
    monkeypatch.setattr(reader, "_opener", Opener())
    with pytest.raises(files.M365FilesError, match=message) as error:
        reader.get(files.GRAPH + "/drives/id/root")
    assert "credential-secret" not in str(error.value)
    assert files._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid") is None


def test_json_response_is_bounded(monkeypatch):
    class Response(io.BytesIO):
        def geturl(self):
            return files.GRAPH + "/drives/id/root"

    class Opener:
        def open(self, request, timeout):
            return Response(b"x" * 11)

    reader = files.GraphReader("credential-secret")
    monkeypatch.setattr(reader, "_opener", Opener())
    monkeypatch.setattr(files, "_MAX_RESPONSE_BYTES", 10)
    with pytest.raises(files.M365FilesError, match="limit"):
        reader.get(files.GRAPH + "/drives/id/root")


def test_cli_lists_actual_collector_result_and_writes_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("GRAPH_TOKEN", token())
    urls = setup_reader(monkeypatch, [folder(), {"value": [child()]}])
    output = tmp_path / "files.json"
    result = CliRunner().invoke(cli, ["m365-files", "--tenant-id", TENANT, "--drive-id", "drive-1",
                                      "--folder", "Project_ORION", "--token-env", "GRAPH_TOKEN", "--json", "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert len(urls) == 2
    saved = json.loads(output.read_text(encoding="utf8"))
    assert saved["items"][0]["name"] == "Overview.docx"
    assert saved["folder"]["path"] == "Project_ORION"
    assert "signature-secret" not in result.output + output.read_text()
    assert urlsplit(urls[0]).hostname == "graph.microsoft.com"


def test_cli_missing_auth_fails_with_actionable_error_and_no_false_output(tmp_path):
    output = tmp_path / "files.json"
    result = CliRunner().invoke(cli, ["m365-files", "--tenant-id", TENANT, "--drive-id", "drive-1", "-o", str(output)])
    assert result.exit_code != 0
    assert "Choose --copilot-wam" in result.output
    assert not output.exists()


def test_cli_copilot_wam_passes_in_memory_token_to_the_get_collector(monkeypatch, tmp_path):
    from agenthound.discovery import copilot_wam

    tenants = []

    def authenticate(tenant):
        tenants.append(tenant)
        return token()

    monkeypatch.setattr(copilot_wam, "acquire_copilot_graph_token", authenticate)
    urls = setup_reader(monkeypatch, [folder(), {"value": [child()]}])
    output = tmp_path / "files.json"
    result = CliRunner().invoke(cli, ["m365-files", "--copilot-wam", "--tenant-id", TENANT,
                                     "--drive-id", "drive-1", "--json", "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert tenants == [TENANT]
    assert len(urls) == 2
    assert json.loads(output.read_text())["complete"] is True
    assert "signature-secret" not in result.output + output.read_text()


def test_cli_copilot_denial_preserves_existing_output_and_never_contacts_graph(monkeypatch, tmp_path):
    from agenthound.discovery import copilot_wam

    def deny(tenant):
        raise files.M365FilesError("Copilot WAM silent authentication was not granted (AADSTS65002)")

    monkeypatch.setattr(copilot_wam, "acquire_copilot_graph_token", deny)
    urls = setup_reader(monkeypatch, [])
    output = tmp_path / "files.json"
    output.write_text("existing-observation", encoding="utf-8")
    result = CliRunner().invoke(cli, ["m365-files", "--copilot-wam", "--tenant-id", TENANT,
                                     "--drive-id", "drive-1", "-o", str(output)])
    assert result.exit_code != 0
    assert "AADSTS65002" in result.output
    assert output.read_text() == "existing-observation"
    assert urls == []
