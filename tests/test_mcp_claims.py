"""Conservative implementation/advertisement/access boundaries."""

import json

import pytest
from pydantic import ValidationError

from agenthound.collectors.assets import collect_assets
from agenthound.collectors.capabilities import _merge_capabilities
from agenthound.collectors.impact import collect_impact
from agenthound.discovery.config_parser import _build_mcp_capability
from agenthound.discovery.profiles import legacy_fields
from agenthound.models.claims import CapabilityClaim
from agenthound.models.nodes import Capability


def claim(
    resource, action, *, readonly=False, locality="remote", scope="fixed_service"
):
    return CapabilityClaim(
        resource=resource,
        action=action,
        confidence="confirmed",
        evidence="Official exact tool schema defines operation; not effective access.",
        scope=scope,
        locality=locality,
        readonly=readonly,
        source_url="https://example.org/source.py",
        verification_status="verified",
        tools=("operation",),
    )


@pytest.mark.parametrize(
    "resource,action",
    [
        ("database", "query"),
        ("database", "write"),
        ("secrets", "read"),
        ("secrets", "search"),
        ("identity", "manage"),
        ("cloud", "read"),
        ("cloud", "administer"),
        ("kubernetes", "execute"),
        ("container", "execute"),
        ("messaging", "send"),
        ("cicd", "trigger"),
        ("knowledge", "write"),
    ],
)
def test_unsupported_domains_do_not_manufacture_legacy_axes(resource, action):
    fields = legacy_fields((claim(resource, action),))
    assert all(
        fields[key] == "none"
        for key in ("file_access", "shell_exec", "network_send", "git_write")
    )


def test_local_git_commit_and_remote_push_are_distinct():
    local = claim("repository", "commit", locality="local", scope="local_repository")
    remote = claim("repository", "push", scope="remote_repository")
    assert local.locality != remote.locality
    assert legacy_fields((local,))["git_write"] == "confirmed"
    assert legacy_fields((remote,))["git_write"] == "confirmed"
    assert "local_repository" in legacy_fields((local,))["git_evidence"]


def test_fixed_search_does_not_imply_arbitrary_network_send():
    fixed = claim("network", "query", readonly=True)
    arbitrary = claim("network", "read", readonly=True, scope="arbitrary_url")
    assert legacy_fields((fixed,))["network_send"] == "none"
    assert legacy_fields((arbitrary,))["network_send"] == "confirmed"


def test_read_write_are_separate_and_readonly_is_derived():
    read = claim("filesystem", "read", readonly=True, locality="local")
    write = claim("filesystem", "write", locality="local")
    assert legacy_fields((read,))["file_readonly"]
    assert not legacy_fields((read, write))["file_readonly"]


def test_experimental_claim_cannot_be_confirmed():
    data = claim("database", "query", readonly=True).model_dump()
    data["verification_status"] = "experimental"
    with pytest.raises(ValidationError):
        CapabilityClaim.model_validate(data)


def test_claims_export_only_as_flat_string_lists():
    cap = Capability(
        name="example",
        cap_kind="MCPServer",
        claims=(claim("database", "query", readonly=True),),
    )
    props = cap.to_opengraph()["properties"]
    assert isinstance(props["capability_claims"][0], str)
    assert json.loads(props["capability_claims"][0])["resource"] == "database"
    assert all(
        not isinstance(value, dict)
        and (not isinstance(value, list) or all(isinstance(x, str) for x in value))
        for value in props.values()
    )
    assert props["effective_access_confidence"] == "none"


def test_profile_alone_does_not_prove_host_assets_or_impacts(tmp_path):
    (tmp_path / ".env").write_text("VALUE=fixture")
    cap = Capability(
        name="server",
        cap_kind="MCPServer",
        profile_id="example",
        shell_exec="confirmed",
        file_access="confirmed",
        network_send="confirmed",
        git_write="confirmed",
        discovery_status="configured",
        claims=(claim("process", "execute", locality="local"),),
    )
    assert not collect_assets([cap], tmp_path, "workspace").edges
    assert not collect_impact([cap], True).edges


def test_static_tool_policy_filters_read_write_exactly():
    cap = _build_mcp_capability(
        "any-label",
        {
            "command": "npx",
            "args": ["@modelcontextprotocol/server-filesystem"],
            "enabled_tools": ["read_file"],
        },
        "agent",
    )
    assert cap.file_access == "confirmed" and cap.file_readonly
    assert all(c.readonly for c in cap.claims)
    denied = _build_mcp_capability(
        "any-label",
        {
            "command": "npx",
            "args": ["@modelcontextprotocol/server-filesystem"],
            "enabled_tools": [],
        },
        "agent",
    )
    assert not denied.claims and denied.file_access == "none"


def test_merge_retains_claim_provenance_and_policy():
    a = Capability(
        name="server",
        cap_kind="MCPServer",
        profile_id="example",
        identity_confidence="confirmed",
        implementation_confidence="confirmed",
        claims=(claim("database", "query", readonly=True),),
        tool_allowlist_configured=True,
        tool_allowlist=("operation",),
    )
    b = a.model_copy(update={"claims": (claim("database", "write"),)})
    merged = _merge_capabilities(a, b)
    assert len(merged.claims) == 2 and merged.profile_id == "example"
    assert merged.effective_access_confidence == "none"
    assert merged.tool_allowlist_configured


def test_secret_arguments_never_become_identity_or_output():
    cap = _build_mcp_capability(
        "example",
        {
            "command": "uvx",
            "args": ["mcp-server-fetch", "--api-token", "fixture-secret-argument"],
            "env": {"API_KEY": "fixture-secret-environment"},
            "headers": {"Authorization": "fixture-secret-header"},
            "url": "https://user:fixture-password@example.org/mcp?token=fixture-query#fixture-fragment",
        },
        "a",
    )
    text = json.dumps(cap.to_opengraph())
    assert "fixture-" not in text
    assert "API_KEY" in text and "Authorization" in text


def test_merge_reconciles_filters_and_conflicting_identities():
    c = claim("filesystem", "read", readonly=True, locality="local")
    a = Capability(
        name="same",
        cap_kind="MCPServer",
        profile_id="a",
        identity_confidence="confirmed",
        implementation_confidence="confirmed",
        claims=(c,),
    )
    b = a.model_copy(update={"tool_denylist": ("operation",)})
    assert not _merge_capabilities(a, b).claims
    assert _merge_capabilities(a, b).file_access == "none"
    conflict = _merge_capabilities(a, a.model_copy(update={"profile_id": "b"}))
    assert not conflict.profile_id
    assert conflict.identity_confidence == "suspected"
    assert all(c.confidence == "suspected" for c in conflict.claims)


def test_merge_preserves_live_advertisement_metadata():
    a = Capability(
        name="same",
        cap_kind="MCPServer",
        live_advertisement_confidence="confirmed",
        advertised_tool_names=("operation",),
        advertised_tools=1,
        discovery_status="advertised",
    )
    merged = _merge_capabilities(a, a)
    assert merged.live_advertisement_confidence == "confirmed"
    assert (
        merged.advertised_tool_names == ("operation",) and merged.advertised_tools == 1
    )


@pytest.mark.parametrize(
    "command,args",
    [
        ("uvx", ["mcp-server-filesystem"]),
        ("npx", ["mcp-server-git"]),
        ("pipx", ["run", "playwright-mcp"]),
    ],
)
def test_console_script_name_is_not_a_package_in_another_ecosystem(command, args):
    from agenthound.discovery.profiles import resolve_profile

    assert resolve_profile(command, args) is None
