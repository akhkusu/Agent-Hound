import base64
import io
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest
from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.discovery import sharepoint_files as sp
from agenthound.discovery.m365_files import M365FilesError

TENANT = "11111111-1111-4111-8111-111111111111"
AUDIENCE = "33333333-3333-4333-8333-333333333333"
SITE = "https://example.sharepoint.com/sites/team"
ORIGIN = "https://example.sharepoint.com"
ROOT = "/sites/team/Shared Documents/Project"
FOLDER_ID = "44444444-4444-4444-8444-444444444444"
FILE_ID = "55555555-5555-4555-8555-555555555555"


def token(tenant=TENANT, audience=AUDIENCE):
    payload = base64.urlsafe_b64encode(json.dumps({"tid": tenant, "aud": audience}).encode()).decode().rstrip("=")
    return "header." + payload + ".secret-fixture-token"


def file_row(name="文書 #100%.docx", identity=FILE_ID):
    return {"Name": name, "UniqueId": identity, "ServerRelativeUrl": ROOT + "/" + name,
            "Length": "12", "TimeLastModified": "2026-11-01T10:00:00Z"}


def mock_listing(monkeypatch, responses):
    urls = []
    monkeypatch.setattr(sp, "discover_sharepoint_resource", lambda origin, tenant: AUDIENCE)

    class Reader:
        def __init__(self, bearer, origin, api_path):
            assert bearer == token()
            assert origin == ORIGIN

        def get(self, url):
            urls.append(url)
            return responses[len(urls) - 1]

    monkeypatch.setattr(sp, "SharePointReader", Reader)
    return urls


def metadata():
    return {"UniqueId": FOLDER_ID, "ServerRelativeUrl": ROOT, "Name": "Project"}


def collect():
    return sp.collect_sharepoint_files(tenant_id=TENANT, site_url=SITE, folder="Project",
                                      token_provider=lambda tenant, site: token())


def test_sharepoint_lists_files_and_folders_without_tokens_or_raw_fields(monkeypatch):
    row = {**file_row(), "credential": "never-export"}
    folder = {**file_row("Subfolder", "66666666-6666-4666-8666-666666666666")}
    urls = mock_listing(monkeypatch, [metadata(), {"value": [row]}, {"value": [folder]}])
    result = collect()
    assert result["complete"] is True
    assert [item["kind"] for item in result["items"]] == ["file", "folder"]
    assert result["items"][0]["size"] == 12
    assert "%23" in result["items"][0]["web_url"]
    assert "%25" in result["items"][0]["web_url"]
    assert "secret-fixture-token" not in json.dumps(result)
    assert "never-export" not in json.dumps(result)
    assert all(url.startswith(SITE + "/_api/") for url in urls)


@pytest.mark.parametrize("next_link", ["https://other.sharepoint.com/sites/team/_api/files",
                                      SITE + "/_api/another-folder"])
def test_pagination_cannot_move_credentials_to_another_host_or_folder(monkeypatch, next_link):
    urls = mock_listing(monkeypatch, [metadata(), {"value": [], "odata.nextLink": next_link}])
    with pytest.raises(M365FilesError, match="pagination destination"):
        collect()
    assert len(urls) == 2


def test_item_from_another_folder_is_rejected(monkeypatch):
    row = {**file_row(), "ServerRelativeUrl": "/another-folder/document.docx"}
    mock_listing(monkeypatch, [metadata(), {"value": [row]}])
    with pytest.raises(M365FilesError, match="outside the requested folder"):
        collect()


@pytest.mark.parametrize("tenant,audience", [("other", AUDIENCE), (TENANT, "https://graph.microsoft.com")])
def test_token_tenant_and_resource_are_bound_to_discovery(tenant, audience):
    with pytest.raises(M365FilesError, match="SharePoint audience"):
        sp.check_sharepoint_token_route(token(tenant, audience), TENANT, ORIGIN, AUDIENCE)


@pytest.mark.parametrize("realm,expected", [(TENANT, AUDIENCE), ("22222222-2222-4222-8222-222222222222", None)])
def test_auth_challenge_discovers_resource_and_rejects_wrong_tenant(monkeypatch, realm, expected):
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            headers = {"WWW-Authenticate": f'Bearer realm="{realm}",client_id="{AUDIENCE}"'}
            raise HTTPError(request.full_url, 401, "Unauthorized", headers, io.BytesIO(b""))

    monkeypatch.setattr(sp, "build_opener", lambda *args: Opener())
    if expected:
        assert sp.discover_sharepoint_resource(ORIGIN, TENANT) == expected
    else:
        with pytest.raises(M365FilesError, match="requested tenant"):
            sp.discover_sharepoint_resource(ORIGIN, TENANT)
    assert requests[0].get_header("Authorization") == "Bearer"
    assert urlsplit(requests[0].full_url).hostname == "example.sharepoint.com"


def test_cli_sharepoint_uses_the_native_provider_and_limits_saved_items(monkeypatch, tmp_path):
    from agenthound.discovery import copilot_wam

    monkeypatch.setattr(copilot_wam, "acquire_copilot_sharepoint_token", lambda *args, **kwargs: token())
    mock_listing(monkeypatch, [metadata(), {"value": [file_row(), file_row("two.docx", "77777777-7777-4777-8777-777777777777")]}, {"value": []}])
    output = tmp_path / "listing.json"
    result = CliRunner().invoke(cli, ["m365-files", "--api", "sharepoint", "--tenant-id", TENANT,
                                     "--site-url", SITE, "--folder", "Project", "--limit", "1",
                                     "--json", "-o", str(output)])
    assert result.exit_code == 0, result.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["enumerated_file_count"] == 2
    assert len(data["items"]) == 1 and data["items_truncated"] is True
    assert data["content_access_verified_count"] is None
