"""Export observed ACLs and evidence-scoped Copilot paths to OpenGraph."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agenthound.discovery.m365_snapshot import DocumentReference, M365Snapshot


def _id(tenant: str, category: str, value: str) -> str:
    digest = hashlib.sha256(
        json.dumps([tenant.casefold(), category, value]).encode()
    ).hexdigest()
    return f"m365-{category}-{digest[:32]}"


def build_m365_graph(snapshot: M365Snapshot) -> dict[str, Any]:
    """Map positive ACL evidence; never infer denial, indexing or exfiltration."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    tenant = snapshot.tenant_id

    def node(category: str, identity: str, kinds: list[str], **props: Any) -> str:
        key = _id(tenant, category, identity)
        nodes.append(
            {
                "id": key,
                "kinds": kinds,
                "properties": {
                    "tenant_id": tenant,
                    "resource_id": identity,
                    "observed_at": snapshot.observed_at,
                    **props,
                },
            }
        )
        return key

    def edge(
        start: str,
        end: str,
        kind: str,
        evidence: str,
        confidence: str = "confirmed",
        **props: Any,
    ) -> None:
        edges.append(
            {
                "start": {"value": start, "match_by": "id"},
                "end": {"value": end, "match_by": "id"},
                "kind": kind,
                "properties": {
                    "confidence": confidence,
                    "evidence": evidence,
                    "evidence_source": snapshot.evidence_source,
                    "observed_at": snapshot.observed_at,
                    **props,
                },
            }
        )

    principal_ids = {
        p.id: node(
            "principal",
            p.id,
            ["M365Principal"],
            name=p.name.upper(),
            displayname=p.name,
            principal_kind=p.kind,
        )
        for p in snapshot.principals
    }
    document_ids = {
        d.id: node(
            "document",
            d.id,
            ["Source", "Asset"],
            name=d.name.upper(),
            displayname=d.name,
            source_kind="DocFile",
            asset_kind="SharePointDocument",
            path=d.web_url,
            classification=d.classification,
            acl_complete=d.acl_complete,
            is_external=True,
        )
        for d in snapshot.documents
    }
    for membership in snapshot.memberships:
        edge(
            principal_ids[membership.member_id],
            principal_ids[membership.group_id],
            "MemberOf",
            membership.evidence,
            observed_at=membership.observed_at or snapshot.observed_at,
        )
    for doc in snapshot.documents:
        for grant in doc.grants:
            roles = set(grant.roles)
            for kind in (
                ["CanRead"]
                + (["CanWrite"] if roles & {"write", "owner"} else [])
                + (["Owns"] if "owner" in roles else [])
            ):
                edge(
                    principal_ids[grant.principal_id],
                    document_ids[doc.id],
                    kind,
                    grant.evidence,
                    inheritance=grant.inheritance,
                    roles=sorted(roles),
                    observed_at=grant.observed_at or snapshot.observed_at,
                )

    for agent in snapshot.agents:
        agent_id = node(
            "agent",
            agent.id,
            ["Agent"],
            name=agent.name.upper(),
            displayname=agent.name,
            platform="Microsoft 365 Copilot",
            identity_evidence=agent.evidence,
            observed_at=agent.observed_at or snapshot.observed_at,
        )
        agent_node = nodes[-1]
        edge(agent_id, principal_ids[agent.principal_id], "RunsAs", agent.evidence,
             observed_at=agent.observed_at or snapshot.observed_at)
        reachable = {agent.principal_id}
        while True:
            expanded = reachable | {
                m.group_id for m in snapshot.memberships if m.member_id in reachable
            }
            if expanded == reachable:
                break
            reachable = expanded
        readable = {
            d.id
            for d in snapshot.documents
            if any(g.principal_id in reachable for g in d.grants)
        }
        references: dict[str, DocumentReference] = {}
        for ref in agent.references:
            previous = references.get(ref.document_id)
            if previous is None or ref.mode == "retrieved":
                references[ref.document_id] = ref
        retrieved = {key for key, ref in references.items() if ref.mode == "retrieved"}
        # Observed retrieval is positive access evidence even when ACL collection
        # is incomplete. A configured source, by itself, is not access evidence.
        accessible = readable | retrieved
        agent_node["properties"]["unresolved_reference_count"] = len(
            set(references) - accessible
        )
        if accessible:
            cap_id = node(
                "capability",
                agent.id,
                ["Capability"],
                name="DOCUMENT READING" if retrieved else "SHAREPOINT RETRIEVAL",
                displayname="Document reading (observed)"
                if retrieved
                else "SharePoint retrieval candidate",
                cap_kind="Permission",
                effective_principal=agent.principal_id,
                file_readonly=True,
            )
            edge(
                agent_id,
                cap_id,
                "HasCapability",
                "Document reading observed; inspect each edge's retrieval route"
                if retrieved
                else "User ACLs observed; host entitlement and retrieval unverified",
                confidence="confirmed" if retrieved else "suspected",
            )
            for doc_id in sorted(accessible):
                observed = doc_id in retrieved
                route = references[doc_id].retrieval_route if observed else "unknown"
                context_requirement = (
                    {"requires_explicit_document_context": route == "explicit_document_context"}
                    if route != "unknown" else {}
                )
                edge(
                    cap_id,
                    document_ids[doc_id],
                    "CanAccess",
                    references[doc_id].evidence
                    if observed
                    else "Positive user/group ACL; Copilot retrieval not implied",
                    confidence="confirmed" if observed else "suspected",
                    access_basis="observed_retrieval" if observed else "acl",
                    retrieval_verified=observed,
                    retrieval_route=route,
                    observed_at=(references[doc_id].observed_at or snapshot.observed_at)
                    if observed else snapshot.observed_at,
                    **context_requirement,
                )
        for ref in references.values():
            # A configured source alone cannot override the executing user's ACL.
            if ref.document_id not in accessible:
                continue
            edge(
                document_ids[ref.document_id],
                agent_id,
                "Influences",
                ref.evidence,
                confidence="confirmed" if ref.mode == "retrieved" else "suspected",
                reference_mode=ref.mode,
                retrieval_route=ref.retrieval_route,
                observed_at=ref.observed_at or snapshot.observed_at,
            )
    # Multiple independent grants may produce the same relation; retain their evidence.
    return {
        "metadata": {
            "source_kind": "AgentHound",
            "evidence_source": snapshot.evidence_source,
            "observed_at": snapshot.observed_at,
        },
        "graph": {"nodes": nodes, "edges": edges},
    }
