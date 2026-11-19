"""Combine independently evidenced observations without joining by display name."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote, urlsplit

from agenthound.discovery.m365_snapshot import Document, _validate_timestamp
from agenthound.discovery.sharepoint_scope import (
    is_library_forms_path,
    library_exclusion_reason,
)
from agenthound.output.m365 import _id


def merge_graphs(*payloads: dict[str, Any]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for payload in payloads:
        for node in payload["graph"]["nodes"]:
            if node["id"] in nodes and nodes[node["id"]] != node:
                raise ValueError(f"Conflicting observations for node {node['id']}")
            nodes[node["id"]] = node
        edges.extend(payload["graph"]["edges"])
    return {
        "metadata": {"source_kind": "AgentHound"},
        "graph": {"nodes": list(nodes.values()), "edges": edges},
    }


def build_metadata_graph(listing: dict[str, Any]) -> dict[str, Any]:
    """Model conditional Copilot document-context paths using API listing evidence."""
    if listing.get("authentication") != "copilot_wam":
        raise ValueError("Copilot metadata graph requires a Copilot WAM observation")
    tenant = listing["tenant_id"]
    observed_at = _validate_timestamp(listing["observed_at"])
    session_id = _id(tenant, "copilot-context", observed_at)
    cap_id = _id(tenant, "document-context-capability", observed_at)
    evidence = "SharePoint REST file metadata returned using the configured Copilot WAM account/client"
    props = {"tenant_id": tenant, "observed_at": observed_at,
             "evidence_source": "sharepoint_rest", "evidence": evidence}
    nodes: list[dict[str, Any]] = [{
        "id": session_id, "kinds": ["Agent"],
        "properties": {**props, "name": "MICROSOFT 365 COPILOT",
                       "displayname": "Copilot (SharePoint work-account context)",
                       "platform": "Microsoft 365 Copilot",
                       "observation_kind": "copilot_document_context_candidate",
                       "content_access_verification": "not_tested",
                       "copilot_retrieval_verified": False},
    }, {
        "id": cap_id, "kinds": ["Capability"],
        "properties": {**props, "name": "SHAREPOINT DOCUMENT READ",
                       "displayname": "SharePoint document read (context required)", "cap_kind": "Permission",
                       "file_readonly": True, "content_access_verification": "not_tested"},
    }]

    def edge(start: str, end: str, kind: str) -> dict[str, Any]:
        return {"start": {"value": start, "match_by": "id"},
                "end": {"value": end, "match_by": "id"}, "kind": kind,
                "properties": {**props, "confidence": "suspected",
                               "access_basis": "copilot_document_context_candidate",
                               "metadata_access_verified": True,
                               "access_scope": "document_content",
                               "requires_explicit_document_context": True,
                               "retrieval_route": "explicit_document_context_candidate",
                               "retrieval_verified": False}}

    edges = [edge(session_id, cap_id, "HasCapability")]
    seen = set()
    for item in listing["items"]:
        if item["kind"] != "file":
            continue
        library = item.get("library", "")
        if library_exclusion_reason(library, hidden=item.get("library_hidden") is True):
            continue
        if item.get("site_url") and library:
            prefix = unquote(urlsplit(item["site_url"]).path).rstrip("/") + "/" + library.strip("/") + "/"
            document_path = unquote(urlsplit(item["web_url"]).path)
            if document_path.startswith(prefix) and is_library_forms_path(document_path[len(prefix):]):
                continue
        doc = Document(id=item["web_url"], name=item["name"], web_url=item["web_url"])
        doc_id = _id(tenant, "listed-document", doc.web_url)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        nodes.append({"id": doc_id, "kinds": ["Source", "Asset"], "properties": {
            **props, "name": doc.name, "displayname": doc.name,
            "asset_kind": "SharePointDocument", "path": doc.web_url,
            "source_kind": "DocFile", "is_external": True,
            "site_url": item.get("site_url", ""), "library": library,
            "library_title": item.get("library_title", library),
            "observation_kind": "file_metadata", "content_access_verified": False,
            "content_access_verification": "not_tested",
        }})
        edges.append(edge(cap_id, doc_id, "CanAccess"))
        influence = edge(doc_id, session_id, "Influences")
        influence["properties"]["evidence"] = (
            "Document in an observed content library is a candidate input when explicitly supplied to Copilot; ingestion not observed"
        )
        edges.append(influence)
    nodes[0]["properties"].update({
        "collection_complete": listing.get("complete", False),
        "collection_error_count": len(listing.get("errors", [])),
        "retained_file_count": len(seen),
    })
    if listing.get("enumerated_file_count") is not None:
        nodes[0]["properties"]["enumerated_file_count"] = listing["enumerated_file_count"]
    for key in ("site_count", "enumerated_folder_count", "enumeration_complete", "site_discovery_complete"):
        if listing.get(key) is not None:
            nodes[0]["properties"][key] = listing[key]
    nodes[0]["properties"]["tenant_wide_coverage"] = "unverified"
    nodes[0]["properties"]["inventory_scope"] = "visible_document_and_site_content"
    nodes[0]["properties"]["excluded_library_count"] = len(listing.get("excluded_libraries", []))
    nodes[0]["properties"]["collection_errors"] = [json.dumps(e, sort_keys=True) for e in listing.get("errors", [])]
    return {"metadata": {"source_kind": "AgentHound"},
            "graph": {"nodes": nodes, "edges": edges}}
