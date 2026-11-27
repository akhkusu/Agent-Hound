from agenthound.models.nodes import Agent, Asset, Capability, Impact, Source


def test_agent_objectid_deterministic():
    a1 = Agent(name="claude-desktop", platform="Claude Desktop", config_path="/x")
    a2 = Agent(name="claude-desktop", platform="Claude Desktop", config_path="/x")
    assert a1.objectid == a2.objectid


def test_agent_objectid_changes_with_path():
    a1 = Agent(name="claude-desktop", platform="Claude Desktop", config_path="/x")
    a2 = Agent(name="claude-desktop", platform="Claude Desktop", config_path="/y")
    assert a1.objectid != a2.objectid


def test_agent_to_opengraph_structure():
    agent = Agent(name="claude-desktop", platform="Claude Desktop", config_path="/home/.config/Claude/config.json")
    node = agent.to_opengraph()
    assert node["id"] == agent.objectid
    assert node["kinds"] == ["Agent"]
    assert node["properties"]["name"] == "CLAUDE-DESKTOP"
    assert node["properties"]["displayname"] == "Claude Desktop"
    assert "objectid" not in node["properties"]


def test_source_objectid_unique_per_path():
    s1 = Source(name="README", source_kind="DocFile", path="/a/README.md")
    s2 = Source(name="README", source_kind="DocFile", path="/b/README.md")
    assert s1.objectid != s2.objectid


def test_source_to_opengraph():
    s = Source(name="README.md", source_kind="DocFile", path="/srv/README.md", is_external=False)
    node = s.to_opengraph()
    assert node["kinds"] == ["Source"]
    assert node["properties"]["name"] == "README.md"
    assert node["properties"]["displayname"] == "README.md"
    assert node["properties"]["filename"] == "README.md"
    assert node["properties"]["internal_name"] == "README.md"
    assert node["properties"]["source_kind"] == "DocFile"
    assert node["properties"]["is_external"] is False


def test_file_source_uses_filename_without_changing_identity_or_type():
    source = Source(
        name="Claude Agent: reviewer",
        source_kind="AgentDefinition",
        path=r"C:\project\.claude\agents\reviewer.md",
        platform="Claude Code",
    )
    original_id = source.objectid

    node = source.to_opengraph()

    assert node["id"] == original_id
    assert node["kinds"] == ["Source"]
    assert node["properties"]["name"] == "reviewer.md"
    assert node["properties"]["displayname"] == "reviewer.md"
    assert node["properties"]["internal_name"] == "Claude Agent: reviewer"
    assert node["properties"]["source_kind"] == "AgentDefinition"
    assert node["properties"]["platform"] == "Claude Code"
    assert node["properties"]["path"] == source.path


def test_non_file_source_keeps_semantic_name():
    source = Source(
        name="project-files: private docs",
        source_kind="MCPResource",
        path="https://example.test/docs",
    )

    node = source.to_opengraph()

    assert node["properties"]["name"] == "PROJECT-FILES: PRIVATE DOCS"
    assert node["properties"]["displayname"] == "project-files: private docs"
    assert "filename" not in node["properties"]


def test_capability_has_shell_flag():
    c = Capability(name="bash-hook", cap_kind="ShellHook", has_shell=True)
    assert c.has_shell is True
    node = c.to_opengraph()
    assert node["properties"]["has_shell"] is True


def test_capability_to_opengraph_with_command():
    c = Capability(name="filesystem", cap_kind="MCPServer", command="npx -y server-fs", transport="stdio")
    node = c.to_opengraph()
    assert node["kinds"] == ["Capability"]
    assert node["properties"]["name"] == "MCP: filesystem"
    assert node["properties"]["displayname"] == "MCP: filesystem"
    assert node["properties"]["internal_name"] == "filesystem"
    assert node["properties"]["mcp_server_name"] == "filesystem"
    assert node["properties"]["command"] == "npx -y server-fs"
    assert node["properties"]["transport"] == "stdio"


def test_capability_labels_distinguish_mcp_server_tool_and_builtin():
    mcp_tool = Capability(
        name="project-files: read_file",
        cap_kind="MCPTool",
        tool_name="read_file",
        mcp_server_name="project-files",
    )
    builtin = Capability(name="Bash", cap_kind="BuiltInTool")

    tool_props = mcp_tool.to_opengraph()["properties"]
    builtin_props = builtin.to_opengraph()["properties"]

    assert tool_props["name"] == "MCP Tool: read_file"
    assert tool_props["displayname"] == "MCP Tool: read_file"
    assert tool_props["internal_name"] == "project-files: read_file"
    assert tool_props["mcp_server_name"] == "project-files"
    assert tool_props["tool_name"] == "read_file"
    assert builtin_props["name"] == "Bash"
    assert builtin_props["displayname"] == "Bash"
    assert not builtin_props["name"].startswith("MCP:")
    assert "mcp_server_name" not in builtin_props


def test_same_mcp_server_name_stays_distinct_per_agent_without_truncation():
    name = "project-files-with-a-long-untruncated-name"
    first = Capability(name=name, cap_kind="MCPServer", agent_scope="agent-1")
    second = Capability(name=name, cap_kind="MCPServer", agent_scope="agent-2")

    assert first.objectid != second.objectid
    assert first.to_opengraph()["properties"]["name"] == f"MCP: {name}"
    assert second.to_opengraph()["properties"]["name"] == f"MCP: {name}"


def test_asset_objectid_from_path():
    a1 = Asset(name="id_rsa", asset_kind="SSHKey", path="/home/.ssh/id_rsa")
    a2 = Asset(name="id_rsa", asset_kind="SSHKey", path="/home/.ssh/id_rsa")
    assert a1.objectid == a2.objectid


def test_asset_to_opengraph():
    a = Asset(name="id_rsa", asset_kind="SSHKey", path="/home/.ssh/id_rsa", readable=True, writable=False)
    node = a.to_opengraph()
    assert node["kinds"] == ["Asset"]
    assert node["properties"]["asset_kind"] == "SSHKey"
    assert node["properties"]["readable"] is True
    assert node["properties"]["writable"] is False


def test_impact_to_opengraph():
    i = Impact(name="internet-exfiltration", impact_kind="Exfiltration", reachable=True,
               description="Agent can send data externally")
    node = i.to_opengraph()
    assert node["kinds"] == ["Impact"]
    assert node["properties"]["impact_kind"] == "Exfiltration"
    assert node["properties"]["reachable"] is True
