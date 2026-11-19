import json

from agenthound.discovery.mcp_live import MCPAdvertisement, _advertisement_result
from agenthound.models.nodes import Capability


def test_advertised_mcp_metadata_becomes_graph_nodes_without_content_access():
    server = Capability(
        name="demo",
        cap_kind="MCPServer",
        agent_scope="agent-1",
        discovery_status="configured",
    )
    advertisement = MCPAdvertisement(
        protocol_version="2026-07-28",
        server_name="demo-server",
        server_version="1.2.3",
        tools=({
            "name": "write_file",
            "description": "Writes a file",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "never-export-default"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },),
        resources=({
            "name": "private docs",
            "uri": "https://example.com/docs?token=never-export-uri",
            "mimeType": "text/plain",
        },),
        prompts=({
            "name": "review",
            "description": "Review a change",
            "arguments": [{"name": "diff", "required": True}],
        },),
    )

    result = _advertisement_result(server, advertisement, "2026-11-01T00:00:00+00:00")

    updated = next(cap for cap in result.capabilities if cap.cap_kind == "MCPServer")
    tool = next(cap for cap in result.capabilities if cap.cap_kind == "MCPTool")
    assert updated.discovery_status == "advertised"
    assert updated.advertised_tools == 1
    assert tool.file_access == "suspected"
    assert tool.destructive_hint is True
    assert "never-export-default" not in tool.input_schema
    assert {edge.kind for edge in result.edges} == {"Exposes"}
    assert {source.source_kind for source in result.sources} == {"MCPResource", "MCPPrompt"}
    resource = next(source for source in result.sources if source.source_kind == "MCPResource")
    assert resource.path == "https://example.com/docs"
    assert "never-export" not in json.dumps([
        source.to_opengraph() for source in result.sources
    ])
