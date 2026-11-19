"""Offline, table-driven identity and capability checks for every catalog entry."""

from copy import deepcopy
import json
from urllib.parse import urlsplit

import pytest

from agenthound.discovery.config_parser import _build_mcp_capability
from agenthound.discovery.profile_registry import load_profiles, validate_registry
from agenthound.discovery.profiles import (
    claims_for_profile,
    legacy_fields,
    resolve_profile,
)
from agenthound.models.claims import CapabilityClaim


PROFILES = load_profiles()
PROFILE_CASES = [
    pytest.param(profile, id=f"batch_{index // 25 + 1}:{profile.canonical_id}")
    for index, profile in enumerate(PROFILES)
]
PACKAGE_CASES = [
    pytest.param(profile, package, ecosystem, id=f"{profile.canonical_id}:{package}")
    for profile in PROFILES
    for package, ecosystem in profile.package_ecosystems.items()
]
EXECUTABLE_CASES = [
    pytest.param(profile, executable, id=f"{profile.canonical_id}:{executable}")
    for profile in PROFILES
    for executable in profile.executables
]
MODULE_CASES = [
    pytest.param(profile, module, id=f"{profile.canonical_id}:{module}")
    for profile in PROFILES
    for module in profile.modules
]
CONTAINER_CASES = [
    pytest.param(profile, image, id=f"{profile.canonical_id}:{image}")
    for profile in PROFILES
    for image in profile.containers
]
ENDPOINT_CASES = [
    pytest.param(profile, endpoint, id=f"{profile.canonical_id}:{endpoint}")
    for profile in PROFILES
    for endpoint in profile.endpoints
]


def _assert_profile(command, args, profile, *, url=None):
    match = resolve_profile(command, args, url)
    assert match is not None
    assert match[0].canonical_id == profile.canonical_id


def _config(profile):
    if profile.packages:
        package = profile.packages[0]
        launcher = "npx" if profile.package_ecosystems[package] == "npm" else "uvx"
        return {"command": launcher, "args": [package]}
    if profile.executables:
        return {"command": profile.executables[0]}
    if profile.modules:
        return {"command": "python", "args": ["-m", profile.modules[0]]}
    if profile.containers:
        return {"command": "docker", "args": ["run", "--rm", profile.containers[0]]}
    return {"url": profile.endpoints[0]}


def _record_with_claims():
    return next(profile for profile in PROFILES if profile.claims).model_dump(
        mode="json"
    )


def test_registry_contains_at_least_one_hundred_distinct_implementations():
    assert len(PROFILES) >= 100
    assert len({profile.canonical_id for profile in PROFILES}) == len(PROFILES)


@pytest.mark.parametrize("profile", PROFILE_CASES)
def test_every_profile_has_reviewable_metadata_and_valid_normalized_claims(profile):
    assert profile.display_name and profile.maintainer and profile.category
    assert profile.verified_ref and profile.verified_date
    assert profile.evidence and profile.limitations
    for source in (profile.repository_url, profile.documentation_url):
        assert urlsplit(source).scheme == "https"
        assert urlsplit(source).hostname
    assert any(
        (
            profile.packages,
            profile.executables,
            profile.modules,
            profile.containers,
            profile.endpoints,
        )
    )
    if profile.status == "recognized_only":
        assert not profile.claims
    for claim in profile.claims:
        assert CapabilityClaim.model_validate(claim.model_dump()) == claim
        assert claim.tools
        assert claim.source_url.startswith("https://")
        assert claim.confidence in {"none", "suspected", "confirmed"}
        assert (
            claim.verification_status != "experimental"
            or claim.confidence != "confirmed"
        )


@pytest.mark.parametrize("profile", PROFILE_CASES)
def test_every_profile_identity_builds_generic_mcp_node_without_effective_access(
    profile,
):
    capability = _build_mcp_capability(
        "arbitrary-user-label", _config(profile), "agent"
    )
    assert capability.cap_kind == "MCPServer"
    assert capability.profile_id == profile.canonical_id
    assert capability.identity_confidence == "confirmed"
    assert capability.effective_access_confidence == "none"
    if profile.status == "recognized_only":
        assert not capability.claims
        assert capability.file_access == capability.shell_exec == "none"
        assert capability.network_send == capability.git_write == "none"
    properties = capability.to_opengraph()["properties"]
    for value in properties.values():
        assert isinstance(value, (str, int, float, bool, list))
        if isinstance(value, list):
            assert all(isinstance(item, (str, int, float, bool)) for item in value)


@pytest.mark.parametrize("profile,package,ecosystem", PACKAGE_CASES)
def test_all_package_identities_and_versions_match_supported_launchers(
    profile, package, ecosystem
):
    if ecosystem == "npm":
        for launcher in ("npx", "bunx", r"C:\tools\npx.cmd", "/opt/bin/bunx"):
            for spec in (package, package + "@1.2.3", package + "@latest"):
                _assert_profile(launcher, ["--yes", spec], profile)
        _assert_profile("npx", ["--cache", "/tmp/cache", "--", package], profile)
    else:
        for launcher, prefix in (
            ("uvx", []),
            (r"C:\tools\uvx.exe", []),
            ("pipx", ["run"]),
        ):
            for spec in (package, package + "==1.2.3", package + "[extra]==1.2.3"):
                _assert_profile(launcher, prefix + [spec], profile)
        _assert_profile("uvx", ["--python", "3.12", package], profile)


@pytest.mark.parametrize("profile", PROFILE_CASES)
def test_declared_aliases_match_without_counting_as_new_implementations(profile):
    for alias in (*profile.aliases, *profile.compatibility_aliases):
        # Aliases are exact, published package/console identities. Compatibility
        # aliases remain explicit data and never create an additional profile.
        matches = [resolve_profile(runner, [alias]) for runner in ("npx", "uvx")]
        assert any(
            match and match[0].canonical_id == profile.canonical_id for match in matches
        )


@pytest.mark.parametrize("profile,executable", EXECUTABLE_CASES)
def test_all_native_executable_identities_match_platform_paths(profile, executable):
    _assert_profile(executable, [], profile)
    _assert_profile("/opt/tools/" + executable, ["--token", "private-value"], profile)
    _assert_profile("C:\\tools\\" + executable + ".EXE", [], profile)


@pytest.mark.parametrize("profile,module", MODULE_CASES)
def test_all_python_module_identities_match_without_guessing_script_paths(
    profile, module
):
    for launcher in ("python", "python3", "python3.12", r"C:\Python\python.exe"):
        _assert_profile(launcher, ["-u", "-m", module], profile)
    assert resolve_profile("python", ["/workspace/" + module + ".py"]) is None


@pytest.mark.parametrize("profile,image", CONTAINER_CASES)
def test_all_container_identities_match_tags_digests_and_runner_options(profile, image):
    for spec in (image, image + ":1.2.3", image + "@sha256:" + "a" * 64):
        _assert_profile("docker", ["run", "--rm", "-i", spec], profile)
        _assert_profile(
            r"C:\Docker\docker.exe",
            [
                "run",
                "-e",
                "TOKEN=private-value",
                "--volume",
                "/tmp:/data",
                spec,
            ],
            profile,
        )
    assert resolve_profile("docker", ["run", image + "-similar"]) is None


@pytest.mark.parametrize("profile,endpoint", ENDPOINT_CASES)
def test_all_remote_endpoints_match_exact_host_and_path_after_sanitization(
    profile, endpoint
):
    _assert_profile(None, [], profile, url=endpoint)
    _assert_profile(None, [], profile, url=endpoint + "?token=private-value#fragment")
    parsed = urlsplit(endpoint)
    evil_host = parsed.hostname + ".attacker.invalid"
    assert resolve_profile(None, [], endpoint.replace(parsed.netloc, evil_host)) is None
    assert (
        resolve_profile(None, [], "https://attacker.invalid/mcp?next=" + endpoint)
        is None
    )
    assert resolve_profile(None, [], endpoint + "/unrelated") is None


@pytest.mark.parametrize("profile,package,ecosystem", PACKAGE_CASES)
def test_similar_packages_and_identity_tokens_in_argument_data_never_match(
    profile, package, ecosystem
):
    launcher = "npx" if ecosystem == "npm" else "uvx"
    assert resolve_profile(launcher, [package + "-unrelated"]) is None
    assert resolve_profile(launcher, ["different-unregistered-mcp", package]) is None
    assert resolve_profile(launcher, ["--unknown-option", package]) is None
    assert resolve_profile("node", ["/workspace/" + package]) is None
    assert resolve_profile("unregistered-mcp", ["--repository", package]) is None


@pytest.mark.parametrize("profile", PROFILE_CASES)
def test_user_server_labels_never_recognize_a_product(profile):
    capability = _build_mcp_capability(profile.canonical_id, {}, "agent")
    assert capability.profile_id == ""
    assert capability.identity_confidence == "none"
    assert not capability.claims
    assert "confirmed" not in {
        capability.file_access,
        capability.shell_exec,
        capability.network_send,
        capability.git_write,
    }


@pytest.mark.parametrize(
    "command,args",
    [
        (
            "npx",
            [
                "@modelcontextprotocol/server-filesystem@https://attacker.invalid/mcp.tgz"
            ],
        ),
        ("npx", ["@modelcontextprotocol/server-filesystem@github:attacker/repo"]),
        ("npx", ["@modelcontextprotocol/server-filesystem@file:/tmp/mcp"]),
        ("npx", ["@modelcontextprotocol/server-filesystem@../mcp"]),
        ("uvx", ["mcp-server-git@https://attacker.invalid/mcp.whl"]),
        ("uvx", ["--from", "mcp-server-git", "unregistered-mcp"]),
        ("uvx", ["--from", "attacker-package", "mcp-server-git"]),
        (
            "uvx",
            [
                "--from",
                "mcp-server-git@https://attacker.invalid/payload.whl",
                "mcp-server-git",
            ],
        ),
        ("pipx", ["run", "--spec", "attacker-package", "mcp-server-git"]),
        ("npx", ["--package", "attacker-package", "mcp-server-filesystem"]),
        (
            "npx",
            [
                "--package",
                "@modelcontextprotocol/server-filesystem",
                "unregistered-mcp",
            ],
        ),
        ("npx", ["--call", "@modelcontextprotocol/server-filesystem"]),
        ("docker", ["run", "--entrypoint", "known-mcp", "unregistered-image"]),
        ("bash", ["start-server.sh"]),
        ("python", ["/tmp/git/mcp_server_git.py"]),
        ("node", ["/workspace/filesystem/index.js"]),
    ],
)
def test_source_override_specs_and_launcher_data_do_not_prove_official_identity(
    command, args
):
    assert resolve_profile(command, args) is None


@pytest.mark.parametrize("profile,image", CONTAINER_CASES)
def test_invalid_container_digests_do_not_prove_identity(profile, image):
    assert resolve_profile("docker", ["run", image + "@not-a-digest"]) is None
    assert resolve_profile("docker", ["run", image + "@sha256:short"]) is None


@pytest.mark.parametrize(
    "command,args",
    [
        ("bash", ["start-server.sh"]),
        ("sh", ["/tmp/git/start.sh"]),
        (r"C:\Windows\PowerShell.exe", ["start.ps1"]),
    ],
)
def test_shell_launcher_alone_is_not_an_agent_controlled_shell_capability(
    command, args
):
    capability = _build_mcp_capability(
        "ordinary-label", {"command": command, "args": args}, "agent"
    )
    assert capability.shell_exec == "none"
    assert not capability.has_shell


def test_keywords_in_paths_and_option_values_do_not_create_capabilities():
    capability = _build_mcp_capability(
        "ordinary-label",
        {
            "command": "unregistered-mcp",
            "args": [
                "--workspace",
                "/home/user/git/cache",
                "--output",
                "/tmp/filesystem",
                "--mode",
                "shell",
            ],
        },
        "agent",
    )
    assert capability.file_access == capability.shell_exec == "none"
    assert capability.network_send == capability.git_write == "none"
    assert capability.profile_id == ""


def test_registry_loader_never_uses_runtime_network_or_executes_packages(monkeypatch):
    import socket
    import subprocess
    import urllib.request

    def forbidden(*args, **kwargs):
        raise AssertionError("registry loading must remain offline and static")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    load_profiles.cache_clear()
    assert load_profiles() == PROFILES


@pytest.mark.parametrize(
    "invalid", ["certain", "high", "advertised", "CONFIRMED", 1, None]
)
def test_registry_rejects_invalid_confidence(invalid):
    record = _record_with_claims()
    record["claims"][0]["confidence"] = invalid
    with pytest.raises(ValueError):
        validate_registry([record])


@pytest.mark.parametrize(
    "field,value",
    [
        ("verified_date", "not-a-date"),
        ("verified_ref", ""),
        ("repository_url", "http://example.com/source"),
        ("documentation_url", "https://user:password@example.com/docs"),
        ("documentation_url", "https://example.com/docs?token=private-value"),
        ("status", "unknown"),
    ],
)
def test_registry_rejects_invalid_profile_metadata(field, value):
    record = _record_with_claims()
    record[field] = value
    with pytest.raises(ValueError):
        validate_registry([record])


def test_registry_rejects_duplicate_canonical_ids_and_alias_collisions():
    first = _record_with_claims()
    with pytest.raises(ValueError, match="duplicate canonical"):
        validate_registry([first, deepcopy(first)])
    second = deepcopy(first)
    second["canonical_id"] = first["canonical_id"] + "-other"
    first["aliases"] = second["aliases"] = ["fixture-alias"]
    with pytest.raises(ValueError):
        validate_registry([first, second])


def test_registry_rejects_alias_conflicts_with_another_package_identity():
    first, second = [
        profile.model_dump(mode="json") for profile in PROFILES if profile.packages
    ][:2]
    first["aliases"] = [second["packages"][0]]
    with pytest.raises(ValueError, match="alias conflicts"):
        validate_registry([first, second])


def test_registry_rejects_recognized_only_claims_and_experimental_confirmation():
    record = _record_with_claims()
    record["status"] = "recognized_only"
    with pytest.raises(ValueError, match="recognized-only"):
        validate_registry([record])
    record["status"] = "experimental"
    with pytest.raises(ValueError, match="experimental"):
        validate_registry([record])


def test_registry_requires_machine_identity_and_package_ecosystems():
    record = _record_with_claims()
    for name in ("packages", "executables", "modules", "containers", "endpoints"):
        record[name] = []
    record["package_ecosystems"] = {}
    with pytest.raises(ValueError, match="machine-readable"):
        validate_registry([record])
    record = next(profile for profile in PROFILES if profile.packages).model_dump(
        mode="json"
    )
    record["package_ecosystems"] = {}
    with pytest.raises(ValueError, match="ecosystem"):
        validate_registry([record])


@pytest.mark.parametrize(
    "field,value",
    [
        ("resource", "vendor_specific_resource"),
        ("action", "vendor_specific_action"),
        ("tools", []),
        ("source_url", "https://example.com/tool?token=private-value"),
        ("source_url", "http://example.com/tool"),
    ],
)
def test_registry_rejects_invalid_claims(field, value):
    record = _record_with_claims()
    record["claims"][0][field] = value
    with pytest.raises(ValueError):
        validate_registry([record])


def _claim(
    resource, action, *, readonly=False, scope="implementation", locality="unknown"
):
    return CapabilityClaim(
        resource=resource,
        action=action,
        confidence="confirmed",
        evidence="fixture official tool",
        scope=scope,
        readonly=readonly,
        locality=locality,
        source_url="https://example.com/source",
        verification_status="verified",
        tools=("fixture_tool",),
    )


@pytest.mark.parametrize(
    "resource,action",
    [
        ("database", "query"),
        ("database", "write"),
        ("secrets", "read"),
        ("cloud", "read"),
        ("cloud", "write"),
        ("cloud", "execute"),
        ("identity", "manage"),
        ("kubernetes", "execute"),
        ("messaging", "send"),
        ("email", "send"),
        ("cicd", "trigger"),
        ("knowledge", "write"),
        ("observability", "query"),
        ("productivity", "write"),
    ],
)
def test_unsupported_domains_do_not_inherit_unrelated_legacy_dimensions(
    resource, action
):
    fields = legacy_fields((_claim(resource, action),))
    assert fields["file_access"] == fields["shell_exec"] == "none"
    assert fields["git_write"] == fields["network_send"] == "none"


def test_read_write_local_commit_remote_push_and_arbitrary_network_are_distinct():
    readonly = legacy_fields(
        (_claim("filesystem", "read", readonly=True, locality="local"),)
    )
    writable = legacy_fields((_claim("filesystem", "write", locality="local"),))
    assert readonly["file_access"] == writable["file_access"] == "confirmed"
    assert readonly["file_readonly"] and not writable["file_readonly"]
    local = _claim("repository", "commit", locality="local")
    remote = _claim("repository", "push", locality="remote")
    assert (
        legacy_fields((local,))["git_write"]
        == legacy_fields((remote,))["git_write"]
        == "confirmed"
    )
    assert json.loads(local.flat_property())["locality"] == "local"
    assert json.loads(remote.flat_property())["locality"] == "remote"
    assert (
        legacy_fields((_claim("network", "search", readonly=True, scope="fixed_api"),))[
            "network_send"
        ]
        == "none"
    )
    assert (
        legacy_fields(
            (_claim("network", "read", readonly=True, scope="arbitrary_url"),)
        )["network_send"]
        == "confirmed"
    )
    assert (
        legacy_fields(
            (_claim("browser", "navigate", readonly=True, scope="arbitrary_url"),)
        )["network_send"]
        == "confirmed"
    )
    assert legacy_fields((_claim("process", "execute"),))["shell_exec"] == "confirmed"


@pytest.mark.parametrize("profile", PROFILE_CASES)
def test_allowlists_denylists_and_empty_allowlist_constrain_every_profile(profile):
    assert claims_for_profile(profile, allowlist_configured=True) == ()
    tools = tuple(tool for claim in profile.claims for tool in claim.tools)
    assert claims_for_profile(profile, denylist=tools) == ()
    assert claims_for_profile(profile, advertised_tools=()) == ()
    for claim in profile.claims:
        selected = claim.tools[0]
        claims = claims_for_profile(profile, (selected,), allowlist_configured=True)
        assert all(item.tools == (selected,) for item in claims)
        assert (
            claims_for_profile(
                profile, (selected,), (selected,), allowlist_configured=True
            )
            == ()
        )


def test_configured_credentials_never_appear_in_opengraph_properties():
    profile = next(profile for profile in PROFILES if profile.packages)
    config = _config(profile)
    config["args"] = [
        *config.get("args", []),
        "--token",
        "never-export-argument-secret",
    ]
    config.update(
        env={"API_TOKEN": "never-export-environment-secret"},
        headers={"Authorization": "Bearer never-export-header-secret"},
        url="https://username:never-export-password@example.com/mcp?token=never-export-query-secret#private",
    )
    capability = _build_mcp_capability("ordinary-label", config, "agent")
    encoded = json.dumps(capability.to_opengraph())
    assert "never-export" not in encoded
    assert "username:" not in encoded
    assert "API_TOKEN" in encoded and "Authorization" in encoded


@pytest.mark.parametrize(
    "command",
    [
        "npx @modelcontextprotocol/server-filesystem --token=never-export-secret",
        "tool\x00never-export-secret",
        "tool --password never-export-secret",
    ],
)
def test_unparsed_command_strings_do_not_export_embedded_arguments(command):
    capability = _build_mcp_capability("ordinary-label", {"command": command}, "agent")
    assert "never-export-secret" not in json.dumps(capability.to_opengraph())
