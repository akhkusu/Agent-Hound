"""List immediate folder children with delegated SharePoint REST GETs."""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import Request, build_opener
from uuid import UUID

from agenthound.discovery.m365_files import (
    M365FilesError,
    _folder_path,
    _NoRedirect,
    _site_url,
    validate_tenant,
)

_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def discover_sharepoint_resource(origin: str, tenant: str) -> str:
    """Bind a requested host to its tenant and resource without sending a token."""
    host, path, canonical = _site_url(origin)
    if path or canonical != "https://" + host:
        raise M365FilesError("Resource discovery requires a canonical SharePoint origin")
    request = Request(origin + "/_vti_bin/client.svc", headers={"Authorization": "Bearer"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=20):
            raise M365FilesError("SharePoint did not return an authentication challenge")
    except HTTPError as exc:
        if exc.code != 401:
            raise M365FilesError("SharePoint resource discovery did not return HTTP 401") from None
        challenge = exc.headers.get("WWW-Authenticate", "")
        exc.close()
    except (URLError, TimeoutError, OSError):
        raise M365FilesError("SharePoint resource discovery failed or timed out") from None
    realms = re.findall(r'\brealm="([0-9a-fA-F-]{36})"', challenge, re.IGNORECASE)
    clients = re.findall(r'\bclient_id="([0-9a-fA-F-]{36})"', challenge, re.IGNORECASE)
    try:
        if len(realms) != 1 or len(clients) != 1 or str(UUID(realms[0])) != tenant:
            raise ValueError()
        return str(UUID(clients[0]))
    except ValueError:
        raise M365FilesError("SharePoint authentication challenge does not identify the requested tenant and resource") from None


def check_sharepoint_token_route(token: str, tenant: str, origin: str, audience: str) -> None:
    """Routing guard only; SharePoint verifies the signature and authorization."""
    try:
        if not isinstance(token, str) or len(token) > 65536 or any(c.isspace() for c in token):
            raise ValueError()
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if claims.get("tid", "").lower() != tenant or claims.get("aud") not in (
            audience, origin, origin + "/",
        ):
            raise ValueError()
    except (ValueError, IndexError, TypeError, AttributeError, UnicodeError):
        raise M365FilesError("Token is not a readable JWT for the requested tenant and SharePoint audience") from None


class SharePointReader:
    def __init__(self, token: str, origin: str, api_path: str) -> None:
        self._token = token
        self._origin = origin
        self._api_path = api_path
        self._opener = build_opener(_NoRedirect())

    def get(self, url: str) -> dict[str, Any]:
        parts = urlsplit(url)
        if (parts.scheme + "://" + parts.netloc != self._origin
                or not parts.path.startswith(self._api_path) or parts.fragment
                or "\\" in url or any(ord(c) < 32 for c in url)):
            raise M365FilesError("Rejected an unexpected SharePoint URL")
        request = Request(url, headers={"Authorization": "Bearer " + self._token,
                                        "Accept": "application/json;odata=nometadata"})
        try:
            with self._opener.open(request, timeout=20) as response:
                if response.geturl() != url:
                    raise M365FilesError("SharePoint response unexpectedly changed destination")
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise M365FilesError("SharePoint response exceeded the 4 MiB limit")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise M365FilesError("SharePoint returned an invalid JSON object")
            return data
        except HTTPError as exc:
            raise M365FilesError(f"SharePoint request failed (HTTP {exc.code}); check the site, folder and delegated access") from None
        except (URLError, TimeoutError, OSError):
            raise M365FilesError("SharePoint network request failed or timed out") from None
        except M365FilesError:
            raise
        except (ValueError, UnicodeError):
            raise M365FilesError("SharePoint returned invalid JSON") from None


def _uid(value: Any) -> str:
    try:
        return str(UUID(str(value)))
    except ValueError:
        raise M365FilesError("SharePoint returned an invalid item identity") from None


def collect_sharepoint_files(
    *, tenant_id: str, site_url: str, folder: str = "", library: str = "Shared Documents",
    copilot_wam: bool = False, token_env: str | None = None,
    copilot_client_id: str | None = None,
    token_provider: Callable[[str, str], str] | None = None,
    max_items: int = 10000, max_pages: int = 100,
) -> dict[str, Any]:
    tenant = validate_tenant(tenant_id)
    host, encoded_site_path, canonical_site = _site_url(site_url)
    origin = "https://" + host
    relative = _folder_path(folder)
    library = _folder_path(library)
    if not library or max_items < 1 or max_pages < 1:
        raise M365FilesError("Provide a document library and positive collection limits")
    if sum((bool(copilot_wam), token_env is not None, token_provider is not None)) != 1:
        raise M365FilesError("Choose exactly one SharePoint authentication provider")
    if copilot_client_id is not None and not copilot_wam:
        raise M365FilesError("--copilot-client-id requires --copilot-wam")
    audience = discover_sharepoint_resource(origin, tenant)
    if copilot_wam:
        from agenthound.discovery.copilot_wam import acquire_copilot_sharepoint_token

        token = acquire_copilot_sharepoint_token(tenant, canonical_site,
                                                audience=audience, client_id=copilot_client_id)
    elif token_provider is not None:
        token = token_provider(tenant, canonical_site)
    else:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env or ""):
            raise M365FilesError("--token-env must name an environment variable")
        token = os.environ.get(token_env or "", "")
    check_sharepoint_token_route(token, tenant, origin, audience)
    site_path = unquote(encoded_site_path)
    if site_path and "/" + _folder_path(site_path) != site_path:
        raise M365FilesError("Use a canonical site path")
    target = site_path + "/" + library + ("/" + relative if relative else "")
    api_path = encoded_site_path + "/_api/"
    # ResourcePath takes a decoded path. Escape OData quotes before URL encoding.
    lookup = canonical_site + "/_api/web/GetFolderByServerRelativePath(decodedUrl='" + quote(target.replace("'", "''"), safe="/") + "')"
    client = SharePointReader(token, origin, api_path)
    metadata = client.get(lookup + "?" + urlencode({"$select": "UniqueId,ServerRelativeUrl,Name"}))
    folder_id = _uid(metadata.get("UniqueId"))
    if metadata.get("ServerRelativeUrl") != target:
        raise M365FilesError("SharePoint resolved a different folder than requested")
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    pages = 0
    for collection, kind in (("Files", "file"), ("Folders", "folder")):
        endpoint = lookup + "/" + collection
        fields = "Name,UniqueId,ServerRelativeUrl,TimeLastModified" + (",Length" if kind == "file" else "")
        next_url: str | None = endpoint + "?" + urlencode({"$select": fields, "$top": 200})
        seen_pages: set[str] = set()
        while next_url:
            # A continuation may change only query parameters, never the target collection.
            parts = urlsplit(next_url)
            if parts.scheme + "://" + parts.netloc != origin or parts.path != urlsplit(endpoint).path or parts.fragment:
                raise M365FilesError("Rejected an unexpected SharePoint pagination destination")
            if next_url in seen_pages or pages >= max_pages:
                raise M365FilesError("SharePoint pagination repeated or exceeded the page limit")
            seen_pages.add(next_url)
            pages += 1
            data = client.get(next_url)
            values = data.get("value")
            if not isinstance(values, list):
                raise M365FilesError("SharePoint returned an invalid folder collection")
            for row in values:
                if not isinstance(row, dict):
                    raise M365FilesError("SharePoint returned an invalid folder item")
                name, path = row.get("Name"), row.get("ServerRelativeUrl")
                if (not isinstance(name, str) or not name or "/" in name or "\\" in name
                        or name in (".", "..") or any(ord(c) < 32 for c in name)
                        or path != target + "/" + name):
                    raise M365FilesError("SharePoint returned an item outside the requested folder")
                identity = _uid(row.get("UniqueId"))
                if identity in seen_ids:
                    raise M365FilesError("SharePoint returned duplicate item identities")
                seen_ids.add(identity)
                try:
                    length = row.get("Length") if kind == "file" else 0
                    if isinstance(length, bool) or not isinstance(length, (str, int)):
                        raise TypeError()
                    size = int(length)
                    if size < 0:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise M365FilesError("SharePoint returned an invalid item size") from None
                modified = row.get("TimeLastModified")
                if not isinstance(modified, str) or not modified:
                    raise M365FilesError("SharePoint returned an invalid modified timestamp")
                items.append({"id": identity, "parent_item_id": folder_id,
                              "name": name, "kind": kind, "size": size,
                              "last_modified": modified, "web_url": origin + quote(path, safe="/")})
                if len(items) > max_items:
                    raise M365FilesError("Folder exceeded --max-items; no complete listing was produced")
            candidate = data.get("odata.nextLink", data.get("@odata.nextLink"))
            if candidate is not None and (not isinstance(candidate, str) or not candidate):
                raise M365FilesError("SharePoint returned an invalid pagination link")
            next_url = candidate
    return {"schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
            "evidence_source": "sharepoint_rest", "tenant_id": tenant,
            "authentication": "copilot_wam" if copilot_wam else "explicit_provider",
            "resource_discovery": {"origin": origin, "tenant_id": tenant, "audience": audience},
            "site": {"web_url": canonical_site}, "library": library,
            "folder": {"id": folder_id, "path": relative, "web_url": origin + quote(target, safe="/")},
            "items": items, "page_count": pages, "complete": True,
            "limitations": ["Immediate children only; document contents and complete ACLs were not read.",
                            "Listing access is not proof of Copilot document retrieval.",
                            "The folder can change during pagination; this is not a transactional snapshot."]}
