from agenthound.collectors.impact import collect_impact
from agenthound.models.nodes import Capability


def test_shell_cap_triggers_system_takeover():
    cap = Capability(name="bash-hook", cap_kind="ShellHook", has_shell=True)
    result = collect_impact(capabilities=[cap], internet_reachable=True)
    kinds = {i.impact_kind for i in result.impacts}
    assert "SystemTakeover" in kinds


def test_system_takeover_edge_from_shell_cap():
    cap = Capability(name="bash-hook", cap_kind="ShellHook", has_shell=True)
    result = collect_impact(capabilities=[cap], internet_reachable=True)
    takeover = next(i for i in result.impacts if i.impact_kind == "SystemTakeover")
    edges = [e for e in result.edges if e.end == takeover.objectid]
    assert any(e.start == cap.objectid for e in edges)


def test_network_cap_with_internet_triggers_exfiltration():
    cap = Capability(name="fetch-url", cap_kind="MCPServer")
    result = collect_impact(capabilities=[cap], internet_reachable=True)
    kinds = {i.impact_kind for i in result.impacts}
    assert "Exfiltration" in kinds


def test_network_cap_without_internet_no_exfiltration():
    cap = Capability(name="fetch-url", cap_kind="MCPServer")
    result = collect_impact(capabilities=[cap], internet_reachable=False)
    kinds = {i.impact_kind for i in result.impacts}
    assert "Exfiltration" not in kinds


def test_git_cap_triggers_supply_chain(tmp_path):
    # Create a git repo with a remote
    import subprocess
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/test/repo.git"], capture_output=True)

    cap = Capability(name="git-push", cap_kind="MCPServer", command="git push")
    result = collect_impact(capabilities=[cap], internet_reachable=False, source_repos=[str(repo)])
    kinds = {i.impact_kind for i in result.impacts}
    assert "SupplyChainContamination" in kinds


def test_no_relevant_caps_no_impacts():
    cap = Capability(name="memory", cap_kind="MCPServer")
    result = collect_impact(capabilities=[cap], internet_reachable=False)
    assert result.impacts == []
    assert result.edges == []


def test_triggers_edge_kind():
    cap = Capability(name="bash-hook", cap_kind="ShellHook", has_shell=True)
    result = collect_impact(capabilities=[cap], internet_reachable=False)
    assert all(e.kind == "Triggers" for e in result.edges)
