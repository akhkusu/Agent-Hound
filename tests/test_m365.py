import copy
import json

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from agenthound.cli import cli
from agenthound.discovery.m365_snapshot import M365Snapshot
from agenthound.output.m365 import build_m365_graph


def test_explicit_document_context_preserves_route_and_fact_times(snapshot):
    snapshot["documents"][1]["grants"][0]["observed_at"] = "2026-11-01T08:00:00Z"
    snapshot["agents"][0]["observed_at"] = "2026-11-01T08:30:00Z"
    snapshot["agents"][0]["references"] = [{
        "document_id": "secret", "mode": "retrieved", "evidence": "Cited known file content",
        "retrieval_route": "explicit_document_context", "observed_at": "2026-11-01T08:31:00Z",
    }]
    graph = build_m365_graph(M365Snapshot.model_validate(snapshot))["graph"]
    observed = next(e for e in graph["edges"] if e["kind"] == "CanAccess" and e["properties"]["retrieval_verified"])
    assert observed["properties"]["retrieval_route"] == "explicit_document_context"
    assert observed["properties"]["requires_explicit_document_context"] is True
    assert observed["properties"]["observed_at"] == "2026-11-01T08:31:00Z"
    assert any(e["kind"] == "CanRead" and e["properties"]["observed_at"] == "2026-11-01T08:00:00Z" for e in graph["edges"])
    assert next(e for e in graph["edges"] if e["kind"] == "RunsAs")["properties"]["observed_at"] == "2026-11-01T08:30:00Z"
    assert next(n for n in graph["nodes"] if n["kinds"] == ["Capability"])["properties"]["displayname"] == "Document reading (observed)"


def test_per_fact_timestamp_requires_timezone(snapshot):
    snapshot["documents"][0]["grants"][0]["observed_at"] = "2026-11-01T09:00:00"
    with pytest.raises(ValidationError):
        M365Snapshot.model_validate(snapshot)


@pytest.fixture
def snapshot():
    return {
        "schema_version": 1,
        "tenant_id": "tenant-one",
        "observed_at": "2026-11-01T09:00:00Z",
        "evidence_source": "synthetic",
        "principals": [
            {"id": "writer", "name": "Writer", "kind": "user"},
            {"id": "victim", "name": "Victim", "kind": "user"},
            {"id": "members", "name": "Members", "kind": "group"},
        ],
        "memberships": [
            {
                "member_id": "writer",
                "group_id": "members",
                "evidence": "directory export",
            },
            {
                "member_id": "victim",
                "group_id": "members",
                "evidence": "directory export",
            },
        ],
        "documents": [
            {
                "id": "minutes",
                "name": "Minutes.docx",
                "web_url": "https://example.sharepoint.com/minutes.docx",
                "grants": [
                    {
                        "principal_id": "members",
                        "roles": ["write"],
                        "evidence": "ACL export",
                    }
                ],
            },
            {
                "id": "secret",
                "name": "Acquisition.docx",
                "web_url": "https://example.sharepoint.com/secret.docx",
                "classification": "restricted",
                "acl_complete": True,
                "grants": [
                    {
                        "principal_id": "victim",
                        "roles": ["read"],
                        "evidence": "ACL export",
                    }
                ],
            },
        ],
        "agents": [
            {
                "id": "copilot-victim",
                "name": "Copilot Victim",
                "principal_id": "victim",
                "evidence": "observed work account",
                "references": [
                    {
                        "document_id": "minutes",
                        "mode": "configured",
                        "evidence": "agent knowledge source",
                    }
                ],
            }
        ],
    }


def graph(data):
    return build_m365_graph(M365Snapshot.model_validate(data))["graph"]


def test_acl_and_influence_have_separate_evidence(snapshot):
    g = graph(snapshot)
    kinds = [e["kind"] for e in g["edges"]]
    assert kinds.count("CanWrite") == 1
    assert kinds.count("CanRead") == 2
    assert kinds.count("CanAccess") == 2
    influence = next(e for e in g["edges"] if e["kind"] == "Influences")
    assert influence["properties"]["confidence"] == "suspected"
    assert influence["properties"]["evidence"] == "agent knowledge source"
    assert all(
        e["properties"]["confidence"] == "suspected"
        for e in g["edges"]
        if e["kind"] == "CanAccess"
    )
    assert all(e["kind"] != "Triggers" for e in g["edges"])


def test_no_source_reference_means_no_influence(snapshot):
    snapshot["agents"][0]["references"] = []
    assert not any(e["kind"] == "Influences" for e in graph(snapshot)["edges"])


def test_configured_source_cannot_override_acl(snapshot):
    snapshot["agents"][0]["principal_id"] = "writer"
    snapshot["agents"][0]["references"][0]["document_id"] = "secret"
    g = graph(snapshot)
    assert sum(e["kind"] == "CanAccess" for e in g["edges"]) == 1
    assert not any(e["kind"] == "Influences" for e in g["edges"])
    agent = next(n for n in g["nodes"] if n["kinds"] == ["Agent"])
    assert agent["properties"]["unresolved_reference_count"] == 1


def test_retrieved_source_confidence(snapshot):
    snapshot["agents"][0]["references"][0]["mode"] = "retrieved"
    edge = next(e for e in graph(snapshot)["edges"] if e["kind"] == "Influences")
    assert edge["properties"]["confidence"] == "confirmed"
    access = [e for e in graph(snapshot)["edges"] if e["kind"] == "CanAccess"]
    assert sum(e["properties"]["retrieval_verified"] for e in access) == 1
    # An unspecified route must not be represented as autonomous access.
    assert all("requires_explicit_document_context" not in e["properties"] for e in access)


def test_observed_retrieval_survives_incomplete_acl(snapshot):
    snapshot["documents"][0]["grants"] = []
    snapshot["agents"][0]["references"][0]["mode"] = "retrieved"
    g = graph(snapshot)
    assert any(e["kind"] == "Influences" for e in g["edges"])
    assert any(
        e["kind"] == "CanAccess"
        and e["properties"]["access_basis"] == "observed_retrieval"
        for e in g["edges"]
    )


def test_retrieval_overrides_duplicate_configured_reference(snapshot):
    ref = snapshot["agents"][0]["references"][0].copy()
    ref.update(mode="retrieved", evidence="Retrieved document citation")
    snapshot["agents"][0]["references"].insert(0, ref)
    influences = [e for e in graph(snapshot)["edges"] if e["kind"] == "Influences"]
    assert len(influences) == 1
    assert influences[0]["properties"]["confidence"] == "confirmed"


def test_ids_are_rename_stable_and_tenant_scoped(snapshot):
    first = {n["id"] for n in graph(snapshot)["nodes"]}
    snapshot["documents"][0]["name"] = "Renamed.docx"
    assert {n["id"] for n in graph(snapshot)["nodes"]} == first
    snapshot["tenant_id"] = "tenant-two"
    assert not first.intersection(n["id"] for n in graph(snapshot)["nodes"])


def test_group_cycles_terminate_and_unknown_inheritance_stays_unknown(snapshot):
    snapshot["principals"].append({"id": "nested", "name": "Nested", "kind": "group"})
    snapshot["memberships"] += [
        {"member_id": "members", "group_id": "nested", "evidence": "directory"},
        {"member_id": "nested", "group_id": "members", "evidence": "directory"},
    ]
    g = graph(snapshot)
    assert any(e["kind"] == "CanAccess" for e in g["edges"])
    assert all(
        e["properties"]["inheritance"] == "unknown"
        for e in g["edges"]
        if e["kind"] == "CanRead"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s["documents"][0].update(token="must not be accepted"),
        lambda s: s["principals"].append(copy.deepcopy(s["principals"][0])),
        lambda s: s["documents"][0]["grants"][0].update(principal_id="unknown"),
        lambda s: s["memberships"][0].update(group_id="victim"),
        lambda s: s["documents"][0].update(
            web_url="https://example.com/file?token=secret"
        ),
        lambda s: s.update(observed_at="2026-11-01T09:00:00"),
    ],
)
def test_invalid_snapshots_are_rejected(snapshot, mutation):
    mutation(snapshot)
    with pytest.raises(ValidationError):
        M365Snapshot.model_validate(snapshot)


def test_cli_and_no_input_overwrite(tmp_path, snapshot):
    source = tmp_path / "snapshot.json"
    out = tmp_path / "graph.json"
    source.write_text(json.dumps(snapshot), encoding="utf-8")
    args = ["m365-import", "--snapshot", str(source), "-o"]
    result = CliRunner().invoke(cli, args + [str(out)])
    assert result.exit_code == 0, result.output
    result_graph = json.loads(out.read_text())["graph"]
    ids = {n["id"] for n in result_graph["nodes"]}
    assert all(
        e["start"]["value"] in ids and e["end"]["value"] in ids
        for e in result_graph["edges"]
    )
    assert CliRunner().invoke(cli, args + [str(source)]).exit_code != 0
