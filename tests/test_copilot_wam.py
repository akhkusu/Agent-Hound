import asyncio
import base64
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from agenthound.discovery import copilot_wam as wam
from agenthound.discovery.m365_files import M365FilesError, acquire_graph_token

TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"


def account_store(monkeypatch, tmp_path, records):
    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def query(key, name):
        if name == "AccountId":
            return "current-account", 1
        raise FileNotFoundError()

    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(
        HKEY_CURRENT_USER=1, OpenKey=lambda *args: Key(), QueryValueEx=query))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = tmp_path / "Microsoft" / "OneAuth" / "accounts"
    root.mkdir(parents=True)
    for index, record in enumerate(records):
        (root / str(index)).write_text(json.dumps(record), encoding="utf-8")


def record(tenant=TENANT, identity="current-account", wam_id="selected-wam"):
    return {"id": identity, "realm": tenant, "additional_properties": json.dumps({
        "wam_account_ids": json.dumps({CLIENT: wam_id})})}


def test_configuration_joins_exact_account_and_nested_json(monkeypatch, tmp_path):
    account_store(monkeypatch, tmp_path, [record(identity="other"), record()])
    assert wam._configured_wam_account(TENANT) == (CLIENT, "selected-wam")


@pytest.mark.parametrize("records, message", [
    ([record(tenant="another-tenant")], "another tenant"),
    ([record(), record()], "exactly one"),
    ([record(identity="other")], "exactly one"),
])
def test_wrong_or_ambiguous_configuration_is_not_used(monkeypatch, tmp_path, records, message):
    account_store(monkeypatch, tmp_path, records)
    with pytest.raises(M365FilesError, match=message):
        wam._configured_wam_account(TENANT)


def fake_broker(monkeypatch, *, status=0, accounts=None, token_tenant=TENANT):
    requests = []
    payload = base64.urlsafe_b64encode(json.dumps({
        "tid": token_tenant, "aud": "https://graph.microsoft.com"}).encode()).decode().rstrip("=")
    token = "header." + payload + ".secret-token"
    if accounts is None:
        accounts = [SimpleNamespace(id="wrong-wam", properties={"TenantId": TENANT}),
                    SimpleNamespace(id="selected-wam", properties={"TenantId": TENANT})]

    class Broker:
        @staticmethod
        async def find_account_provider_async(*args):
            return "provider"

        @staticmethod
        async def find_all_accounts_async(*args):
            return SimpleNamespace(status=0, accounts=accounts)

        @staticmethod
        async def get_token_silently_async(request, account):
            requests.append((request, account))
            return SimpleNamespace(response_status=status,
                response_error=SimpleNamespace(error_message="secret-response AADSTS65002 account-private"),
                response_data=[SimpleNamespace(token=token)])

    class Request:
        def __init__(self, provider, scope, client):
            self.properties = {}
            self.scope = scope
            self.client = client

    module_name = "winrt.windows.security.authentication.web.core"
    module = ModuleType(module_name)
    module.WebAuthenticationCoreManager = Broker
    module.WebTokenRequest = Request
    module.FindAllWebAccountsStatus = SimpleNamespace(SUCCESS=0)
    module.WebTokenRequestStatus = SimpleNamespace(SUCCESS=0)
    monkeypatch.setitem(sys.modules, module_name, module)
    return requests, token


def test_silent_request_uses_exact_id_graph_resource_and_tenant(monkeypatch):
    requests, token = fake_broker(monkeypatch)
    assert asyncio.run(wam._acquire(TENANT, "selected-wam", client_id=CLIENT)) == token
    request, account = requests[0]
    assert account.id == "selected-wam"
    assert request.properties == {"resource": "https://graph.microsoft.com",
                                  "authority": "https://login.microsoftonline.com/" + TENANT}
    assert request.client == CLIENT


def test_broker_denial_exposes_code_only_and_never_prompts(monkeypatch):
    requests, _ = fake_broker(monkeypatch, status=5)
    with pytest.raises(M365FilesError, match="AADSTS65002") as caught:
        asyncio.run(wam._acquire(TENANT, "selected-wam", client_id=CLIENT))
    assert "secret-response" not in str(caught.value)
    assert "account-private" not in str(caught.value)
    assert len(requests) == 1


def test_ambiguous_wam_id_never_requests_a_token(monkeypatch):
    account = SimpleNamespace(id="selected-wam", properties={"TenantId": TENANT})
    requests, _ = fake_broker(monkeypatch, accounts=[account, account])
    with pytest.raises(M365FilesError, match="ambiguous"):
        asyncio.run(wam._acquire(TENANT, "selected-wam", client_id=CLIENT))
    assert requests == []


def test_wrong_token_tenant_never_reaches_graph(monkeypatch):
    fake_broker(monkeypatch, token_tenant="another-tenant")
    with pytest.raises(M365FilesError, match="requested tenant"):
        asyncio.run(wam._acquire(TENANT, "selected-wam", client_id=CLIENT))


def test_providers_are_explicit_and_exclusive(monkeypatch):
    monkeypatch.setattr(wam, "acquire_copilot_graph_token", lambda tenant: "in-memory-token")
    assert acquire_graph_token(TENANT, copilot_wam=True) == "in-memory-token"
    with pytest.raises(M365FilesError, match="one authentication"):
        acquire_graph_token(TENANT, "TOKEN", copilot_wam=True)


def test_multiple_clients_require_explicit_existing_mapping(monkeypatch, tmp_path):
    data = record()
    second = "33333333-3333-4333-8333-333333333333"
    data["additional_properties"] = json.dumps({"wam_account_ids": json.dumps({
        CLIENT: "selected-wam", second: "another-wam"})})
    account_store(monkeypatch, tmp_path, [data])
    with pytest.raises(M365FilesError, match="specify --copilot-client-id"):
        wam._configured_wam_account(TENANT)
    assert wam._configured_wam_account(TENANT, CLIENT) == (CLIENT, "selected-wam")
    with pytest.raises(M365FilesError, match="no unique client"):
        wam._configured_wam_account(TENANT, "44444444-4444-4444-8444-444444444444")
