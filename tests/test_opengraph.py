import json
from agenthound.collectors.base import CollectionResult
from agenthound.models.edges import Edge
from agenthound.models.nodes import Agent, Asset, Capability, Impact, Source
from agenthound.output.opengraph import build_opengraph, write_opengraph


def _make_result() -> CollectionResult:
    r = CollectionResult()
    r.agents.append(Agent(name="claude-desktop", platform="Claude Desktop", config_path="/p"))
    r.capabilities.append(Capability(name="filesystem", cap_kind="MCPServer"))
    r.sources.append(Source(name="README.md", source_kind="DocFile", path="/p/README.md"))
    r.assets.append(Asset(name="id_rsa", asset_kind="SSHKey", path="/home/.ssh/id_rsa"))
    r.impacts.append(Impact(name="takeover", impact_kind="SystemTakeover"))
    r.edges.append(Edge(
        start=r.agents[0].objectid,
        end=r.capabilities[0].objectid,
        kind="HasCapability",
    ))
    return r


def test_build_opengraph_has_metadata():
    og = build_opengraph(_make_result())
    assert og["metadata"]["source_kind"] == "AgentHound"


def test_build_opengraph_has_no_custom_types():
    og = build_opengraph(_make_result())
    assert "custom_types" not in og


def test_build_opengraph_node_count():
    og = build_opengraph(_make_result())
    assert len(og["graph"]["nodes"]) == 5


def test_build_opengraph_edge_count():
    og = build_opengraph(_make_result())
    assert len(og["graph"]["edges"]) == 1


def test_node_has_id_and_kinds():
    og = build_opengraph(_make_result())
    for node in og["graph"]["nodes"]:
        assert "id" in node
        assert "kinds" in node
        assert len(node["kinds"]) >= 1


def test_edge_has_start_end_kind():
    og = build_opengraph(_make_result())
    for edge in og["graph"]["edges"]:
        assert "start" in edge
        assert "end" in edge
        assert "kind" in edge
        assert edge["start"]["match_by"] == "id"
        assert edge["end"]["match_by"] == "id"


def test_display_labels_do_not_change_node_ids_or_edge_references():
    result = _make_result()
    expected_ids = {
        node.objectid
        for collection in (
            result.agents,
            result.sources,
            result.capabilities,
            result.assets,
            result.impacts,
        )
        for node in collection
    }
    expected_edges = {
        (edge.start, edge.end, edge.kind)
        for edge in result.edges
    }

    graph = build_opengraph(result)["graph"]

    assert {node["id"] for node in graph["nodes"]} == expected_ids
    assert {
        (edge["start"]["value"], edge["end"]["value"], edge["kind"])
        for edge in graph["edges"]
    } == expected_edges


def test_write_opengraph_creates_valid_json(tmp_path):
    out = tmp_path / "output.json"
    og = build_opengraph(_make_result())
    write_opengraph(og, str(out))
    assert out.exists()
    parsed = json.loads(out.read_text())
    assert "graph" in parsed
    assert "nodes" in parsed["graph"]
