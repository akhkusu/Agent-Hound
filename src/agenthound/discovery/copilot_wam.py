"""Opt-in, silent Windows broker authentication using Copilot account metadata.

No token cache, cookie database, credential blob, or private DLL is opened.
The Microsoft identity provider decides whether the client may obtain a token.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from agenthound.discovery.m365_files import (
    M365FilesError,
    _check_token_route,
    validate_tenant,
)

_MAX_ACCOUNT_BYTES = 1024 * 1024
_TIMEOUT = 30


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise TypeError("Expected a metadata object")
    return value


def discover_copilot_context() -> dict[str, Any]:
    """Discover tenant and candidate SharePoint hosts from account metadata."""
    if sys.platform != "win32":
        raise M365FilesError("Copilot context discovery is available only on Windows")
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Copilot\SharedStorage\Auth") as key:
            current, _ = winreg.QueryValueEx(key, "AccountId")
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise M365FilesError("LOCALAPPDATA is unavailable")
        records = []
        for path in (Path(local) / "Microsoft/OneAuth/accounts").iterdir():
            if not path.is_file() or path.stat().st_size > _MAX_ACCOUNT_BYTES:
                continue
            try:
                with path.open("rb") as source:
                    raw = source.read(_MAX_ACCOUNT_BYTES + 1)
                if len(raw) > _MAX_ACCOUNT_BYTES:
                    continue
                data = _object(json.loads(raw.decode("utf-8-sig")))
            except (ValueError, TypeError, UnicodeError):
                continue
            if data.get("id") == current:
                records.append(data)
        if len(records) != 1:
            raise M365FilesError("Copilot configuration does not identify one current account")
        data = records[0]
        tenant = validate_tenant(str(data.get("realm", "")))
        candidates: set[str] = set()
        for field in ("login_name", "email", "realm_name"):
            domain = str(data.get(field, "")).rsplit("@", 1)[-1].lower()
            match = re.fullmatch(r"([a-z0-9][a-z0-9-]*)\.onmicrosoft\.com", domain)
            if match:
                candidates.add("https://" + match[1] + ".sharepoint.com")
        # A sync client's tenant-specific endpoint is another non-secret host hint.
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts") as accounts:
                for index in range(winreg.QueryInfoKey(accounts)[0]):
                    name = winreg.EnumKey(accounts, index)
                    if not name.startswith("Business"):
                        continue
                    with winreg.OpenKey(accounts, name) as account:
                        for field in ("SPOResourceId", "UrlNamespace"):
                            try:
                                value, _ = winreg.QueryValueEx(account, field)
                            except FileNotFoundError:
                                continue
                            from urllib.parse import urlsplit

                            parts = urlsplit(str(value))
                            host = parts.hostname or ""
                            if parts.scheme == "https" and re.fullmatch(r"[a-z0-9-]+\.sharepoint\.com", host):
                                candidates.add("https://" + host.replace("-my.sharepoint.com", ".sharepoint.com"))
        except FileNotFoundError:
            pass
        return {"tenant_id": tenant, "sharepoint_origin_candidates": sorted(candidates)}
    except M365FilesError:
        raise
    except (OSError, ValueError, TypeError):
        raise M365FilesError("Cannot resolve the current Copilot account context") from None


def _configured_wam_account(tenant: str, client_id: str | None = None) -> tuple[str, str]:
    """Join the current Copilot reference to a single client-specific WAM ID."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Copilot\SharedStorage\Auth") as key:
            account_id, _ = winreg.QueryValueEx(key, "AccountId")
            try:
                signed_out, _ = winreg.QueryValueEx(key, "SignedOutValue")
            except FileNotFoundError:
                signed_out = None
        if not isinstance(account_id, str) or not account_id or signed_out:
            raise M365FilesError("Copilot has no unambiguous signed-in account reference")
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise M365FilesError("LOCALAPPDATA is unavailable")
        root = Path(local) / "Microsoft" / "OneAuth" / "accounts"
        if client_id is not None:
            try:
                client_id = str(UUID(client_id))
            except ValueError:
                raise M365FilesError("--copilot-client-id must be an application GUID") from None
        matches: list[tuple[str, str]] = []
        for path in root.iterdir():
            if not path.is_file() or path.stat().st_size > _MAX_ACCOUNT_BYTES:
                continue
            try:
                with path.open("rb") as source:
                    raw = source.read(_MAX_ACCOUNT_BYTES + 1)
                if len(raw) > _MAX_ACCOUNT_BYTES:
                    continue
                data = _object(json.loads(raw.decode("utf-8-sig")))
                if data.get("id") != account_id:
                    continue
                if str(data.get("realm", "")).casefold() != tenant:
                    raise M365FilesError("The configured Copilot account belongs to another tenant")
                additional = _object(data.get("additional_properties", {}))
                mapping = _object(additional.get("wam_account_ids", {}))
                candidates = []
                for mapped_key, wam_id in mapping.items():
                    mapped_client = str(UUID(mapped_key))
                    if isinstance(wam_id, str) and wam_id and (client_id is None or mapped_client == client_id):
                        candidates.append((mapped_client, wam_id))
                if len(candidates) != 1:
                    raise M365FilesError("Copilot account has no unique client mapping; specify --copilot-client-id from your Copilot configuration")
                matches.extend(candidates)
            except M365FilesError:
                raise
            except (ValueError, UnicodeError, TypeError):
                continue
        if len(matches) != 1:
            raise M365FilesError("Copilot configuration does not identify exactly one WAM account")
        return matches[0]
    except M365FilesError:
        raise
    except OSError:
        raise M365FilesError("Cannot read the current user's Copilot account metadata") from None


async def _acquire(tenant: str, wam_id: str, *, client_id: str,
                   resource: str = "https://graph.microsoft.com", audience: str | None = None) -> str:
    try:
        from winrt.windows.security.authentication.web.core import (
            FindAllWebAccountsStatus,
            WebTokenRequest,
            WebTokenRequestStatus,
        )
        from winrt.windows.security.authentication.web.core import (
            WebAuthenticationCoreManager as WAM,
        )
    except ImportError:
        raise M365FilesError("Copilot WAM authentication requires Agent-Hound's windows-auth dependencies") from None

    provider = await asyncio.wait_for(
        WAM.find_account_provider_async("https://login.microsoft.com", "organizations"), _TIMEOUT)
    if provider is None:
        raise M365FilesError("The Windows organizational account provider is unavailable")
    result = await asyncio.wait_for(WAM.find_all_accounts_async(provider, client_id), _TIMEOUT)
    if result.status != FindAllWebAccountsStatus.SUCCESS:
        raise M365FilesError("Windows could not enumerate accounts for the Copilot client")
    accounts = [account for account in result.accounts if account.id == wam_id]
    if len(accounts) != 1:
        raise M365FilesError("The configured WAM account is absent or ambiguous")
    account = accounts[0]
    if str(dict(account.properties).get("TenantId", "")).casefold() != tenant:
        raise M365FilesError("The selected WAM account does not match the requested tenant")
    scope = "https://graph.microsoft.com/.default" if resource == "https://graph.microsoft.com" else ""
    request = WebTokenRequest(provider, scope, client_id)
    request.properties["resource"] = resource
    request.properties["authority"] = "https://login.microsoftonline.com/" + tenant
    response = await asyncio.wait_for(WAM.get_token_silently_async(request, account), _TIMEOUT)
    if response.response_status != WebTokenRequestStatus.SUCCESS:
        error = response.response_error
        codes = sorted(set(re.findall(r"AADSTS\d+", error.error_message or ""))) if error else []
        detail = " (" + ", ".join(codes) + ")" if codes else ""
        raise M365FilesError("Copilot WAM silent authentication was not granted" + detail
                             + "; no interactive login or consent was attempted")
    if len(response.response_data) != 1:
        raise M365FilesError("Windows returned an ambiguous token response")
    token = response.response_data[0].token
    if resource == "https://graph.microsoft.com":
        _check_token_route(token, tenant)
    else:
        from agenthound.discovery.sharepoint_files import check_sharepoint_token_route

        if audience is None:
            raise M365FilesError("SharePoint authentication requires resource discovery")
        check_sharepoint_token_route(token, tenant, resource, audience)
    return token


def acquire_copilot_graph_token(tenant_id: str, client_id: str | None = None) -> str:
    """Return a Graph token in memory, or fail without prompting or raw errors."""
    return _acquire_token(tenant_id, "https://graph.microsoft.com", client_id=client_id)


def acquire_copilot_sharepoint_token(tenant_id: str, site_url: str, *,
                                    audience: str, client_id: str | None = None) -> str:
    """Request a token for the specified SharePoint origin, using the same client."""
    from agenthound.discovery.m365_files import _site_url

    host, _, _ = _site_url(site_url)
    return _acquire_token(tenant_id, "https://" + host, client_id=client_id, audience=audience)


def _acquire_token(tenant_id: str, resource: str, *,
                   client_id: str | None = None, audience: str | None = None) -> str:
    tenant = validate_tenant(tenant_id)
    if sys.platform != "win32":
        raise M365FilesError("Copilot WAM authentication is available only on Windows")
    try:
        selected_client, wam_id = _configured_wam_account(tenant, client_id)
        return asyncio.run(_acquire(tenant, wam_id, client_id=selected_client,
                                    resource=resource, audience=audience))
    except M365FilesError:
        raise
    except Exception:  # noqa: BLE001 -- sanitize broker exceptions at the credential boundary
        # Broker/OS exception messages can contain account data or auth payloads.
        raise M365FilesError("Copilot WAM authentication failed or timed out; no credentials were logged") from None
