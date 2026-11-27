import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from agenthound.discovery.config_parser import _build_mcp_capability
from agenthound.discovery.mcp_live import (
    MCPAdvertisement,
    MCPDiscoveryTarget,
    _advertisement_result,
    _discover_target,
    _safe_uri,
    _target_sensitive_values,
)
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
        tools=(
            {
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
            },
        ),
        resources=(
            {
                "name": "private docs",
                "uri": "https://example.com/docs?token=never-export-uri",
                "mimeType": "text/plain",
            },
        ),
        prompts=(
            {
                "name": "review",
                "description": "Review a change",
                "arguments": [{"name": "diff", "required": True}],
            },
        ),
    )

    result = _advertisement_result(server, advertisement, "2026-11-01T00:00:00+00:00")

    updated = next(cap for cap in result.capabilities if cap.cap_kind == "MCPServer")
    tool = next(cap for cap in result.capabilities if cap.cap_kind == "MCPTool")
    assert updated.discovery_status == "advertised"
    assert updated.advertised_tools == 1
    assert tool.file_access == "suspected"
    assert tool.destructive_hint is True
    assert tool.mcp_server_name == "demo"
    assert tool.to_opengraph()["properties"]["name"] == "MCP Tool: write_file"
    assert "never-export-default" not in tool.input_schema
    assert {edge.kind for edge in result.edges} == {"Exposes"}
    assert {source.source_kind for source in result.sources} == {
        "MCPResource",
        "MCPPrompt",
    }
    resource = next(
        source for source in result.sources if source.source_kind == "MCPResource"
    )
    assert resource.path == "https://example.com/docs"
    assert "never-export" not in json.dumps(
        [source.to_opengraph() for source in result.sources]
    )


def _advertisement(*tools, **kwargs):
    return MCPAdvertisement(
        protocol_version="2026-07-28",
        server_name="fixture",
        server_version="1.0",
        tools=tuple(tools),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("allowlist", "denylist", "configured", "expected"),
    [
        ((), (), False, {"read_file", "write_file"}),
        (("read_file",), (), True, {"read_file"}),
        (("read_file", "write_file"), ("write_file",), True, {"read_file"}),
        ((), (), True, set()),
        ((), ("write_file",), False, {"read_file"}),
        (("READ_FILE",), (), True, set()),
    ],
)
def test_live_tool_filters_use_exact_names_and_denials_win(
    allowlist, denylist, configured, expected
):
    server = Capability(
        name="fixture",
        cap_kind="MCPServer",
        tool_allowlist=allowlist,
        tool_denylist=denylist,
        tool_allowlist_configured=configured,
    )
    result = _advertisement_result(
        server, _advertisement({"name": "read_file"}, {"name": "write_file"}), "now"
    )
    tools = [cap for cap in result.capabilities if cap.cap_kind == "MCPTool"]
    assert {cap.tool_name for cap in tools} == expected
    updated = result.capabilities[0]
    assert updated.advertised_tool_names == ("read_file", "write_file")
    assert updated.live_advertisement_confidence == "confirmed"
    assert all(cap.effective_access_confidence == "none" for cap in tools)


def test_description_never_confirms_a_capability_and_annotations_are_strict():
    server = Capability(name="fixture", cap_kind="MCPServer")
    result = _advertisement_result(
        server,
        _advertisement(
            {
                "name": "lookup",
                "title": "shell execute arbitrary command",
                "description": "write files; shell execute; push repository; read secrets",
                "annotations": {
                    "readOnlyHint": "false",
                    "destructiveHint": 1,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            }
        ),
        "now",
    )
    tool = next(cap for cap in result.capabilities if cap.cap_kind == "MCPTool")
    assert tool.tool_name == "lookup"
    assert (
        tool.shell_exec
        == tool.file_access
        == tool.git_write
        == tool.network_send
        == "none"
    )
    assert tool.claims == ()
    assert tool.read_only_hint is tool.destructive_hint is None
    assert tool.idempotent_hint is True
    assert tool.open_world_hint is False


def test_complete_live_listing_restricts_static_claims_to_advertised_tools():
    server = _build_mcp_capability(
        "fixture",
        {
            "command": "npx",
            "args": ["@modelcontextprotocol/server-filesystem"],
        },
        "agent",
    )
    assert server.file_access == "confirmed"
    assert not server.file_readonly
    result = _advertisement_result(server, _advertisement({"name": "read_file"}), "now")
    updated, tool = result.capabilities
    assert updated.file_access == tool.file_access == "confirmed"
    assert updated.file_readonly and tool.file_readonly
    assert all(claim.readonly for claim in updated.claims)
    assert {claim.verification_status for claim in updated.claims} == {
        "live_advertised"
    }
    assert (
        updated.effective_access_confidence
        == tool.effective_access_confidence
        == "none"
    )


def test_complete_empty_tools_list_removes_implementation_dimensions():
    server = _build_mcp_capability(
        "fixture",
        {
            "command": "npx",
            "args": ["@modelcontextprotocol/server-filesystem"],
        },
        "agent",
    )
    result = _advertisement_result(server, _advertisement(), "now")
    updated = result.capabilities[0]
    assert updated.file_access == "none"
    assert updated.claims == ()


@pytest.mark.parametrize("kwargs", [{"truncated": True}, {"errors": ("tools/list",)}])
def test_incomplete_live_listing_cannot_disprove_static_implementation_claims(kwargs):
    server = _build_mcp_capability(
        "fixture",
        {
            "command": "npx",
            "args": ["@modelcontextprotocol/server-filesystem"],
        },
        "agent",
    )
    result = _advertisement_result(server, _advertisement(**kwargs), "now")
    updated = result.capabilities[0]
    assert updated.claims == server.claims
    assert updated.file_access == server.file_access


def test_live_readonly_annotation_cannot_enable_official_write_claim():
    server = _build_mcp_capability(
        "fixture",
        {
            "command": "npx",
            "args": ["@modelcontextprotocol/server-filesystem"],
        },
        "agent",
    )
    result = _advertisement_result(
        server,
        _advertisement(
            {
                "name": "write_file",
                "annotations": {"readOnlyHint": True},
            }
        ),
        "now",
    )
    assert all(not cap.claims for cap in result.capabilities)
    assert all(cap.file_access == "none" for cap in result.capabilities)


def test_live_tool_presence_does_not_confirm_a_compatibility_only_product_identity():
    server = _build_mcp_capability(
        "fixture",
        {"command": "npx", "args": ["@modelcontextprotocol/server-git"]},
        "agent",
    )
    assert server.identity_confidence == "suspected"
    assert "exact configured implementation identity" not in server.git_evidence
    result = _advertisement_result(
        server, _advertisement({"name": "git_commit"}), "now"
    )
    assert len(result.capabilities) == 2
    for cap in result.capabilities:
        assert cap.identity_confidence == "suspected"
        assert cap.implementation_confidence == "suspected"
        assert cap.live_advertisement_confidence == "confirmed"
        assert cap.git_write == "suspected"
        assert cap.claims and all(
            claim.confidence == "suspected" for claim in cap.claims
        )
        assert all(
            "product identity unverified" in claim.evidence for claim in cap.claims
        )
        assert all("Exact official tool" not in claim.evidence for claim in cap.claims)
        assert cap.effective_access_confidence == "none"


def test_live_metadata_redacts_runtime_credentials_and_bounds_untrusted_text():
    server = Capability(name="fixture", cap_kind="MCPServer")
    target = MCPDiscoveryTarget(
        server=server,
        transport="stdio",
        env={"TOKEN": "env-credential"},
        headers={"Authorization": "Bearer header-credential"},
        args=("--password=argument-credential",),
        url="https://example.com/mcp?token=query-credential",
    )
    secrets = _target_sensitive_values(target)
    text = (
        "env-credential header-credential argument-credential query-credential "
        "https://username:password@example.com/private?token=inline-secret#fragment "
        "Authorization: Bearer opaque-credential\x00\x1b" + "A" * 2000
    )
    advertisement = _advertisement(
        {
            "name": "lookup",
            "description": text,
            "inputSchema": {
                "properties": {
                    "env-credential": {"type": "string", "default": "default-secret"},
                }
            },
        },
        resources=({"name": "resource", "uri": target.url, "description": text},),
    )
    result = _advertisement_result(server, advertisement, "now", secrets)
    encoded = json.dumps(
        [
            *(cap.to_opengraph() for cap in result.capabilities),
            *(source.to_opengraph() for source in result.sources),
        ]
    )
    for secret in (
        "env-credential",
        "header-credential",
        "argument-credential",
        "query-credential",
        "inline-secret",
        "opaque-credential",
        "default-secret",
    ):
        assert secret not in encoded
    assert "username:password" not in encoded
    tool = result.capabilities[1]
    assert len(tool.description) <= 1000
    assert "\x00" not in tool.description and "\x1b" not in tool.description


@pytest.mark.parametrize(
    "uri",
    [
        "https://username:password@example.com:bad/path?token=secret",
        "https://[malformed/path?token=secret",
    ],
)
def test_malformed_advertised_uri_is_omitted(uri):
    assert _safe_uri(uri) == ""


def test_discovery_only_lists_advertisements_and_never_invokes_tools(monkeypatch):
    from agenthound.discovery import mcp_live

    anyio = pytest.importorskip("anyio")
    mcp = pytest.importorskip("mcp")

    calls = []

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def initialize(self):
            calls.append("initialize")
            return SimpleNamespace(
                protocol_version="fixture",
                server_info=SimpleNamespace(name="fixture", version="1"),
                capabilities=SimpleNamespace(tools=True, resources=None, prompts=None),
            )

        async def list_tools(self, **kwargs):
            calls.append("tools/list")
            return SimpleNamespace(
                tools=[SimpleNamespace(model_dump=lambda **_: {"name": "fixture"})],
                next_cursor=None,
            )

        async def call_tool(self, *args, **kwargs):
            raise AssertionError("discovery must never call a tool")

        async def read_resource(self, *args, **kwargs):
            raise AssertionError("discovery must never read a resource")

    @asynccontextmanager
    async def connect(*args):
        yield object(), object()

    monkeypatch.setattr(mcp_live, "_connect", connect)
    monkeypatch.setattr(mcp, "ClientSession", Session)
    server = Capability(name="fixture", cap_kind="MCPServer")
    advertised = anyio.run(_discover_target, MCPDiscoveryTarget(server, "stdio"), 1.0)
    assert calls == ["initialize", "tools/list"]
    assert advertised.tools == ({"name": "fixture"},)
