"""Discover SharePoint locations and enumerate with the current Copilot account."""

from __future__ import annotations

from datetime import datetime, timezone
from time import monotonic
from typing import Any
from urllib.parse import unquote, urlencode

from agenthound.discovery.copilot_wam import (
    acquire_copilot_sharepoint_token,
    discover_copilot_context,
)
from agenthound.discovery.m365_files import M365FilesError, _site_url
from agenthound.discovery.sharepoint_files import (
    SharePointReader,
    collect_sharepoint_files,
    discover_sharepoint_resource,
)
from agenthound.discovery.sharepoint_scope import library_exclusion_reason


def _sites(reader: SharePointReader, origin: str, max_pages: int) -> set[str]:
    sites = {origin}
    for page in range(max_pages):
        query = {"querytext": "'contentclass:STS_Site OR contentclass:STS_Web'",
                 "selectproperties": "'Path'", "rowlimit": 500,
                 "startrow": page * 500, "trimduplicates": "false"}
        data = reader.get(origin + "/_api/search/query?" + urlencode(query))
        try:
            relevant = data["PrimaryQueryResult"]["RelevantResults"]
            rows = relevant["Table"]["Rows"]
            total = int(relevant["TotalRows"])
            if not isinstance(rows, list):
                raise TypeError()
            for row in rows:
                cells = {cell["Key"]: cell["Value"] for cell in row["Cells"]}
                _, _, site = _site_url(cells["Path"])
                sites.add(site)
            if (page + 1) * 500 >= total:
                return sites
            if not rows:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise M365FilesError("SharePoint site search returned an invalid or incomplete result") from None
    raise M365FilesError("SharePoint site search exceeded the page limit")


def collect_copilot_files(
    *, tenant_id: str | None = None, site_url: str | None = None,
    client_id: str | None = None, display_limit: int = 20,
    max_items: int = 10000, max_pages: int = 100, max_folders: int = 10000,
    enumerate_all: bool = False,
) -> dict[str, Any]:
    started = monotonic()
    context = discover_copilot_context()
    tenant = context["tenant_id"]
    if tenant_id is not None and tenant_id.lower() != tenant:
        raise M365FilesError("Requested tenant does not match the configured Copilot account")
    if display_limit < 1 or max_folders < 1:
        raise M365FilesError("Display and traversal limits must be positive")
    candidates = (["https://" + _site_url(site_url)[0]] if site_url
                  else context["sharepoint_origin_candidates"])
    sessions: dict[str, tuple[str, str]] = {}

    def session(site: str, verified_audience: str | None = None) -> tuple[str, str]:
        origin = "https://" + _site_url(site)[0]
        if origin not in sessions:
            audience = verified_audience or discover_sharepoint_resource(origin, tenant)
            token = acquire_copilot_sharepoint_token(tenant, origin, audience=audience, client_id=client_id)
            sessions[origin] = (token, audience)
        return sessions[origin]

    origin = None
    for candidate in candidates:
        try:
            audience = discover_sharepoint_resource(candidate, tenant)
        except M365FilesError:
            # Host hints are not authoritative; only a tenant-bound challenge is accepted.
            continue
        # A verified host with denied authentication is not an invalid host hint.
        # Preserve the actionable authentication error instead of trying other hosts.
        session(candidate, audience)
        origin = candidate
        break
    if origin is None:
        raise M365FilesError("Cannot verify a SharePoint host from Copilot/OneDrive metadata; specify --site-url for this account")
    errors: list[dict[str, str]] = []
    root_token, _ = session(origin)
    root_reader = SharePointReader(root_token, origin, "/_api/")
    if site_url:
        sites = {_site_url(site_url)[2]}
    else:
        try:
            sites = _sites(root_reader, origin, max_pages)
        except M365FilesError as exc:
            sites = {origin}
            errors.append({"stage": "site_discovery", "site": origin, "error": str(exc)})
    items: list[dict[str, Any]] = []
    libraries: list[dict[str, Any]] = []
    excluded_libraries: list[dict[str, str]] = []
    file_count = 0
    folder_count = 0
    visited: set[tuple[str, str, str]] = set()
    stopped_early = False
    for site in sorted(sites):
        if not enumerate_all and (file_count >= display_limit or monotonic() - started >= 45):
            stopped_early = True
            break
        host, encoded_site_path, canonical = _site_url(site)
        site_origin = "https://" + host
        try:
            token, _ = session(site)
            reader = SharePointReader(token, site_origin, encoded_site_path + "/_api/")
            url = canonical + "/_api/web/lists?" + urlencode({
                "$filter": "BaseType eq 1", "$select": "Id,Title,Hidden,BaseTemplate,ItemCount,RootFolder/ServerRelativeUrl",
                "$expand": "RootFolder", "$top": 5000})
            response = reader.get(url)
            values = response.get("value")
            if not isinstance(values, list) or response.get("odata.nextLink") or response.get("@odata.nextLink"):
                raise M365FilesError("SharePoint library discovery was incomplete")
            for library in values:
                if not enumerate_all and (file_count >= display_limit or monotonic() - started >= 45):
                    stopped_early = True
                    break
                root = library.get("RootFolder", {}).get("ServerRelativeUrl")
                prefix = unquote(encoded_site_path) + "/"
                if not isinstance(root, str) or not root.startswith(prefix):
                    raise M365FilesError("SharePoint returned a library outside the requested site")
                library_path = root[len(prefix):]
                reason = library_exclusion_reason(library_path, hidden=library.get("Hidden") is True)
                if reason:
                    excluded_libraries.append({"site_url": canonical, "library": library_path, "reason": reason})
                    continue
                libraries.append({"site_url": canonical, "library": library_path,
                                  "title": library.get("Title", library_path),
                                  "reported_item_count": library.get("ItemCount"),
                                  "count_meaning": "Library-reported items, including folders; not verified readable documents."})
                queue = [""]
                while queue:
                    if not enumerate_all and (file_count >= display_limit or monotonic() - started >= 45):
                        stopped_early = True
                        break
                    relative = queue.pop()
                    key = (canonical, library_path, relative)
                    if key in visited:
                        raise M365FilesError("Repeated folder during recursive enumeration")
                    if len(visited) >= max_folders:
                        raise M365FilesError("Recursive enumeration exceeded the folder limit")
                    visited.add(key)

                    def existing_token(_tenant: str, _site: str, _bearer: str = token) -> str:
                        return _bearer

                    try:
                        listing = collect_sharepoint_files(
                            tenant_id=tenant, site_url=canonical, library=library_path, folder=relative,
                            token_provider=existing_token,
                            max_items=max_items, max_pages=max_pages)
                    except M365FilesError as exc:
                        errors.append({"stage": "folder_listing", "site": canonical,
                                       "library": library_path, "folder": relative, "error": str(exc)})
                        continue
                    folder_count += 1
                    for item in listing["items"]:
                        if item["kind"] == "folder":
                            if not relative and item["name"].casefold() == "forms":
                                continue
                            queue.append((relative + "/" if relative else "") + item["name"])
                        else:
                            file_count += 1
                            if len(items) < display_limit:
                                items.append({**item, "site_url": canonical, "library": library_path,
                                              "library_title": library.get("Title", library_path)})
                            if not enumerate_all and file_count >= display_limit:
                                stopped_early = True
                                break
        except (M365FilesError, AttributeError, TypeError) as exc:
            message = str(exc) if isinstance(exc, M365FilesError) else "Invalid SharePoint library metadata"
            errors.append({"stage": "site_listing", "site": canonical, "error": message})
    return {"schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
            "evidence_source": "sharepoint_rest", "authentication": "copilot_wam",
            "tenant_id": tenant, "mode": "recursive_discovery", "items": items,
            "display_limit": display_limit, "displayed_file_count": len(items),
            "enumerated_file_count": file_count,
            "total_files_in_discovered_libraries": file_count if not errors and not stopped_early else None,
            "content_access_verified_count": None,
            "site_count": len(sites), "enumerated_folder_count": folder_count,
            "libraries": libraries, "excluded_libraries": excluded_libraries, "errors": errors,
            "enumeration_complete": not errors and not stopped_early,
            "stopped_early": stopped_early, "enumerate_all": enumerate_all,
            "site_discovery_complete": bool(site_url),
            "complete": bool(site_url) and not errors and not stopped_early,
            "limitations": ["Only the first display_limit file metadata records are retained.",
                            "Hidden libraries, system libraries and root Forms folders are excluded from document attack paths.",
                            "Default traversal stops after the display limit or a 45-second budget; use --enumerate-all to count discovered locations fully.",
                            "Site search can omit unindexed or search-excluded sites; tenant-wide coverage is not established.",
                            "Library ItemCount is not a count of readable documents and can include folders.",
                            "Enumerated metadata does not establish document-content access; contents were not requested."]}
