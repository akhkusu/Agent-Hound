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
    assert node["properties"]["objectid"] == agent.objectid


def test_source_objectid_unique_per_path():
    s1 = Source(name="README", source_kind="DocFile", path="/a/README.md")
    s2 = Source(name="README", source_kind="DocFile", path="/b/README.md")
    assert s1.objectid != s2.objectid


def test_source_to_opengraph():
    s = Source(name="README.md", source_kind="DocFile", path="/srv/README.md", is_external=False)
    node = s.to_opengraph()
    assert node["kinds"] == ["Source"]
    assert node["properties"]["source_kind"] == "DocFile"
    assert node["properties"]["is_external"] is False


def test_capability_has_shell_flag():
    c = Capability(name="bash-hook", cap_kind="ShellHook", has_shell=True)
    assert c.has_shell is True
    node = c.to_opengraph()
    assert node["properties"]["has_shell"] is True


def test_capability_to_opengraph_with_command():
    c = Capability(name="filesystem", cap_kind="MCPServer", command="npx -y server-fs", transport="stdio")
    node = c.to_opengraph()
    assert node["kinds"] == ["Capability"]
    assert node["properties"]["command"] == "npx -y server-fs"
    assert node["properties"]["transport"] == "stdio"


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
