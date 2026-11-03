"""Read live SharePoint folder metadata through Microsoft Graph.

Authentication is supplied explicitly, independently of the HTTP collector.
An explicit Copilot WAM provider can read account metadata, never cache tokens.
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

GRAPH = "https://graph.microsoft.com/v1.0"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_SELECT = "id,name,size,lastModifiedDateTime,webUrl,file,folder,package,parentReference"


class M365FilesError(ValueError):
    """A user-facing error whose message never contains raw auth/API payloads."""


def validate_tenant(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise M365FilesError("--tenant-id must be the target Entra tenant GUID") from None


def _graph_url(url: str, expected_path: str | None = None) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https" or parts.netloc != "graph.microsoft.com"
        or not parts.path.startswith("/v1.0/") or parts.fragment
        or (expected_path is not None and parts.path != expected_path)
    ):
        raise M365FilesError("Rejected an unexpected Graph URL or pagination destination")
    return url


def _site_url(value: str) -> tuple[str, str, str]:
    parts = urlsplit(value)
    host = parts.hostname or ""
    if (
        parts.scheme != "https" or parts.netloc != host
        or not host.endswith(".sharepoint.com")
        or parts.query or parts.fragment or "\\" in value
        or any(x in (".", "..") for x in parts.path.split("/"))
    ):
        raise M365FilesError("Use a canonical https://<tenant>.sharepoint.com site URL without query or fragment")
    path = parts.path.rstrip("/")
    return host, path, "https://" + host + path


def _folder_path(value: str) -> str:
    path = value.replace("\\", "/")
    if path.startswith("//") or ":" in path or any(ord(c) < 32 for c in path):
        raise M365FilesError("--folder must be relative to the document library root")
    segments = path.strip("/").split("/") if path.strip("/") else []
    if any(segment in ("", ".", "..") for segment in segments):
        raise M365FilesError("--folder cannot contain empty, . or .. path segments")
    return "/".join(segments)


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise M365FilesError(f"Graph returned an invalid {label}")
    return value


def _web_url(value: Any) -> str:
    if not isinstance(value, str):
        raise M365FilesError("Graph returned a missing item web URL")
    parts = urlsplit(value)
    if (
        parts.scheme != "https" or not parts.hostname
        or parts.netloc != parts.hostname or not parts.hostname.endswith(".sharepoint.com")
        or parts.fragment or "\\" in value or any(ord(c) < 32 for c in value)
    ):
        raise M365FilesError("Graph returned a noncanonical SharePoint item URL")
    if parts.query:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        parameters = dict(pairs)
        try:
            if (
                not parts.path.lower().endswith("/_layouts/15/doc.aspx")
                or len(pairs) != len(parameters)
                or set(parameters) - {"sourcedoc", "file", "action", "mobileredirect"}
                or any(ord(c) < 32 for v in parameters.values() for c in v)
            ):
                raise ValueError()
            UUID(parameters["sourcedoc"].strip("{}"))
        except (ValueError, KeyError):
            raise M365FilesError("Graph item URL has an unexpected document query") from None
    return value


def _check_token_route(token: str, tenant: str) -> None:
    """Use unverified JWT metadata only as a routing guard, not authentication.

    Microsoft Graph remains responsible for signature/authorization validation.
    An opaque token is rejected because the requested tenant cannot be checked.
    """
    try:
        if len(token) > 65536 or any(c.isspace() for c in token):
            raise ValueError()
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        audience = claims.get("aud")
        if claims.get("tid", "").lower() != tenant or audience not in (
            "https://graph.microsoft.com", "https://graph.microsoft.com/",
            "00000003-0000-0000-c000-000000000000",
        ):
            raise ValueError()
    except (ValueError, IndexError, TypeError, AttributeError, UnicodeError):
        raise M365FilesError("Token is not a readable JWT for the requested tenant and Microsoft Graph audience") from None


def acquire_graph_token(tenant_id: str, token_env: str | None = None,
                        copilot_wam: bool = False, copilot_client_id: str | None = None) -> str:
    tenant = validate_tenant(tenant_id)
    if copilot_wam:
        if token_env is not None:
            raise M365FilesError("Use one authentication provider: --copilot-wam or --token-env")
        from agenthound.discovery.copilot_wam import acquire_copilot_graph_token

        return (acquire_copilot_graph_token(tenant, copilot_client_id)
                if copilot_client_id is not None else acquire_copilot_graph_token(tenant))
    if copilot_client_id is not None:
        raise M365FilesError("--copilot-client-id requires --copilot-wam")
    if token_env is not None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env):
            raise M365FilesError("--token-env must name an environment variable, not contain a token")
        token = os.environ.get(token_env, "")
        if not token:
            raise M365FilesError(f"Token environment variable {token_env} is unset or empty")
        _check_token_route(token, tenant)
        return token
    raise M365FilesError(
        "Choose --copilot-wam for opt-in silent Windows authentication. "
        "For an explicit existing Graph token, use --token-env VARIABLE_NAME; "
        "this does not reuse the Copilot login and does not invoke Azure CLI."
    )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


class GraphReader:
    """GET-only client. Bearer credentials are never forwarded across redirects."""

    def __init__(self, token: str, timeout: float = 20) -> None:
        self._token = token
        self._timeout = timeout
        self._opener = build_opener(_NoRedirect())

    def get(self, url: str) -> dict[str, Any]:
        _graph_url(url)
        request = Request(url, headers={"Authorization": "Bearer " + self._token, "Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.geturl() != url:
                    raise M365FilesError("Graph response unexpectedly changed destination")
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise M365FilesError("Graph response exceeded the 4 MiB limit")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise M365FilesError("Graph returned an invalid JSON object")
            return data
        except HTTPError as exc:
            if exc.code == 401:
                message = "Graph authentication failed (HTTP 401); refresh the target tenant login"
            elif exc.code == 403:
                message = "Graph access denied (HTTP 403); check delegated scopes, consent and the user's site/file access"
            elif exc.code == 404:
                message = "Graph resource not found or not visible (HTTP 404); check site, drive and library-relative folder"
            elif exc.code == 429:
                message = "Graph throttled the request (HTTP 429); retry later"
            elif 300 <= exc.code < 400:
                message = "Graph redirect rejected; bearer credentials were not forwarded"
            else:
                message = f"Graph request failed (HTTP {exc.code})"
            raise M365FilesError(message) from None
        except (URLError, TimeoutError, OSError):
            raise M365FilesError("Graph network request failed or timed out") from None
        except M365FilesError:
            raise
        except (ValueError, UnicodeError):
            raise M365FilesError("Graph returned invalid JSON") from None


def collect_m365_files(
    *, tenant_id: str, folder: str = "", site_url: str | None = None,
    drive_id: str | None = None, token_env: str | None = None,
    max_items: int = 10000, max_pages: int = 100,
    token_provider: Callable[[str], str] | None = None,
    copilot_wam: bool = False,
    copilot_client_id: str | None = None,
) -> dict[str, Any]:
    """List a single folder's immediate children; never fetch document contents."""
    tenant = validate_tenant(tenant_id)
    relative = _folder_path(folder)
    if not site_url and not drive_id:
        raise M365FilesError("Provide --site-url or --drive-id")
    if site_url and drive_id:
        raise M365FilesError("Use --site-url or --drive-id, not both")
    site = _site_url(site_url) if site_url else None
    if drive_id:
        drive_id = _identity(drive_id, "drive ID")
    if max_items < 1 or max_pages < 1:
        raise M365FilesError("Collection limits must be positive")
    if token_provider is not None:
        if token_env is not None or copilot_wam or copilot_client_id is not None:
            raise M365FilesError("Use one authentication provider")
        token = token_provider(tenant)
        _check_token_route(token, tenant)
    else:
        token = acquire_graph_token(tenant, token_env, copilot_wam, copilot_client_id)
    client = GraphReader(token)
    observed_site: dict[str, str] | None = None
    if site is not None:
        host, path, canonical = site
        result = client.get(GRAPH + "/sites/" + host + ":" + quote(path or "/", safe="/") + "?$select=id,webUrl")
        site_id = _identity(result.get("id"), "site ID")
        if _web_url(result.get("webUrl")).rstrip("/").casefold() != canonical.casefold():
            raise M365FilesError("Resolved Graph site does not match the requested site URL")
        observed_site = {"id": site_id, "web_url": canonical}
        drive = client.get(GRAPH + "/sites/" + quote(site_id, safe="") + "/drive?$select=id,webUrl")
        drive_id = _identity(drive.get("id"), "drive ID")
    assert drive_id is not None
    drive_path = GRAPH + "/drives/" + quote(drive_id, safe="")
    folder_url = drive_path + ("/root:/" + quote(relative, safe="/") if relative else "/root")
    target = client.get(folder_url + "?" + urlencode({"$select": _SELECT}))
    if not isinstance(target.get("folder"), dict) and not isinstance(target.get("package"), dict):
        raise M365FilesError("The requested path is not a folder")
    folder_id = _identity(target.get("id"), "folder ID")
    folder_web_url = _web_url(target.get("webUrl"))
    children_url = drive_path + "/items/" + quote(folder_id, safe="") + "/children"
    next_url: str | None = children_url + "?" + urlencode({"$select": _SELECT, "$top": 200})
    expected_path = urlsplit(children_url).path
    seen_pages: set[str] = set()
    seen_ids: set[str] = set()
    items: list[dict[str, Any]] = []
    pages = 0
    while next_url:
        _graph_url(next_url, expected_path)
        if next_url in seen_pages or pages >= max_pages:
            raise M365FilesError("Pagination repeated or exceeded the page limit; no complete listing was produced")
        seen_pages.add(next_url)
        pages += 1
        response = client.get(next_url)
        values = response.get("value")
        if not isinstance(values, list):
            raise M365FilesError("Graph returned an invalid children collection")
        for value in values:
            if not isinstance(value, dict):
                raise M365FilesError("Graph returned an invalid child item")
            item_id = _identity(value.get("id"), "item ID")
            if item_id in seen_ids:
                raise M365FilesError("Graph returned duplicate item IDs during pagination; retry the listing")
            seen_ids.add(item_id)
            parent = value.get("parentReference", {})
            if not isinstance(parent, dict) or parent.get("driveId", drive_id) != drive_id or parent.get("id", folder_id) != folder_id:
                raise M365FilesError("Graph returned a child outside the requested folder")
            size = value.get("size")
            if type(size) is not int or size < 0:
                raise M365FilesError("Graph returned an invalid item size")
            kind = "folder" if isinstance(value.get("folder"), dict) else "package" if isinstance(value.get("package"), dict) else "file" if isinstance(value.get("file"), dict) else "other"
            items.append({
                "id": item_id, "drive_id": drive_id, "parent_item_id": folder_id,
                "name": _identity(value.get("name"), "item name"), "kind": kind,
                "size": size, "last_modified": _identity(value.get("lastModifiedDateTime"), "modified timestamp"),
                "web_url": _web_url(value.get("webUrl")),
            })
            if len(items) > max_items:
                raise M365FilesError("Folder exceeded --max-items; no complete listing was produced")
        candidate = response.get("@odata.nextLink")
        if candidate is not None and not isinstance(candidate, str):
            raise M365FilesError("Graph returned an invalid pagination link")
        next_url = candidate
    return {
        "schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "evidence_source": "microsoft_graph", "tenant_id": tenant,
        "site": observed_site, "drive_id": drive_id,
        "folder": {"id": folder_id, "path": relative, "web_url": folder_web_url},
        "items": items, "page_count": pages, "complete": True,
        "limitations": [
            "Immediate children only; subfolders and document contents were not read.",
            "Listing access is the API caller's access, not proof of Copilot retrieval or complete ACLs.",
            "A folder may change while pages are collected; this is not a transactional snapshot.",
        ],
    }
