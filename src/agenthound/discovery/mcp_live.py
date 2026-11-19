"""Opt-in MCP discovery that never invokes tools or reads exposed content."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, AsyncIterator, Callable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from agenthound.collectors.base import CollectionResult
from agenthound.discovery.profiles import claims_for_profile, get_profile, legacy_fields
from agenthound.models.edges import Edge
from agenthound.models.nodes import Capability, Source

_MAX_ITEMS = 200
_MAX_PAGES = 10
_MAX_TEXT = 1000
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CREDENTIAL = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*(?:bearer\s+|basic\s+)?|"
    r"(?:api[_-]?key|access[_-]?token|token|password|secret|cookie)\s*[:=]\s*)"
    r"[^\s,;]+"
)
_INLINE_URL = re.compile(r"https?://[^\s<>\"']+")


class MCPDiscoveryUnavailable(RuntimeError):
    """Raised when the optional MCP SDK is not installed."""


@dataclass(frozen=True)
class MCPDiscoveryTarget:
    server: Capability
    transport: str
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    cwd: str = ""


@dataclass(frozen=True)
class MCPAdvertisement:
    protocol_version: str
    server_name: str
    server_version: str
    tools: tuple[dict[str, Any], ...] = ()
    resources: tuple[dict[str, Any], ...] = ()
    resource_templates: tuple[dict[str, Any], ...] = ()
    prompts: tuple[dict[str, Any], ...] = ()
    truncated: bool = False
    errors: tuple[str, ...] = ()


def _require_sdk() -> None:
    try:
        import mcp  # noqa: F401
    except ImportError as exc:
        raise MCPDiscoveryUnavailable(
            "Live MCP discovery requires the optional SDK: pip install 'agenthound[mcp]'"
        ) from exc


def _read_config(path: Path) -> dict[str, Any]:
    if path.suffix == ".toml":
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]
        with path.open("rb") as config_file:
            raw = tomllib.load(config_file)
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("configuration root is not an object")
    return raw


def _find_definition(
    raw: dict[str, Any], server: Capability, workspace: Path
) -> dict[str, Any]:
    if server.mcp_scope == "local":
        projects = raw.get("projects", {})
        project = (
            projects.get(str(workspace.resolve()), {})
            if isinstance(projects, dict)
            else {}
        )
        definitions = project.get("mcpServers", {}) if isinstance(project, dict) else {}
    else:
        definitions = raw.get("mcp_servers", {})
        if not definitions:
            definitions = raw.get("mcpServers", {})
        if not definitions:
            definitions = raw.get("servers", {})
        if not definitions and isinstance(raw.get("mcp"), dict):
            definitions = raw["mcp"].get("servers", {})
    definition = definitions.get(server.name) if isinstance(definitions, dict) else None
    if not isinstance(definition, dict):
        raise ValueError("server definition is unavailable")
    if definition.get("enabled") is False:
        raise ValueError("server is disabled")
    return definition


def _expand(value: Any, environment: dict[str, str]) -> str:
    text = str(value)
    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in environment:
            missing.add(name)
            return match.group(0)
        return environment[name]

    expanded = _ENV_PATTERN.sub(replace, text)
    if missing:
        raise ValueError("unresolved environment placeholder")
    return expanded


def _load_target(server: Capability, workspace: Path) -> MCPDiscoveryTarget:
    if not server.mcp_config_path:
        raise ValueError("configuration path is unavailable")
    path = Path(server.mcp_config_path)
    definition = _find_definition(_read_config(path), server, workspace)

    configured_env = definition.get("env", {})
    env: dict[str, str] = {}
    if isinstance(configured_env, dict):
        for name, value in configured_env.items():
            env[str(name)] = _expand(value, dict(os.environ) | env)
    inherited = definition.get("env_vars", [])
    if isinstance(inherited, list):
        for name in inherited:
            if str(name) in os.environ:
                env[str(name)] = os.environ[str(name)]
    expansion_env = dict(os.environ) | env

    headers: dict[str, str] = {}
    for key in ("headers", "http_headers"):
        configured_headers = definition.get(key, {})
        if isinstance(configured_headers, dict):
            for name, value in configured_headers.items():
                headers[str(name)] = _expand(value, expansion_env)
    env_headers = definition.get("env_http_headers", {})
    if isinstance(env_headers, dict):
        for name, variable in env_headers.items():
            if str(variable) in os.environ:
                headers[str(name)] = os.environ[str(variable)]
    bearer_variable = definition.get("bearer_token_env_var")
    if bearer_variable and str(bearer_variable) in os.environ:
        headers["Authorization"] = f"Bearer {os.environ[str(bearer_variable)]}"

    url = _expand(definition.get("url", ""), expansion_env)
    command = _expand(definition.get("command", ""), expansion_env)
    configured_args = definition.get("args", [])
    if not isinstance(configured_args, list):
        raise ValueError("MCP arguments must be a list")
    args = tuple(_expand(arg, expansion_env) for arg in configured_args)
    transport = str(
        definition.get("type")
        or definition.get("transport")
        or ("http" if url else "stdio")
    ).lower()
    if transport in {"streamable-http", "streamable_http"}:
        transport = "http"
    if url:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("remote MCP URL must use HTTP or HTTPS")
        if parsed.username or parsed.password:
            raise ValueError("credentials in MCP URLs are not supported")
    elif not command:
        raise ValueError("stdio MCP command is unavailable")
    return MCPDiscoveryTarget(
        server=server,
        transport=transport,
        command=command,
        args=args,
        env=env,
        url=url,
        headers=headers,
        cwd=str(workspace.resolve()),
    )


@asynccontextmanager
async def _connect(
    target: MCPDiscoveryTarget, timeout: float
) -> AsyncIterator[tuple[Any, Any]]:
    if target.transport == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        parameters = StdioServerParameters(
            command=target.command,
            args=list(target.args),
            env=target.env,
            cwd=target.cwd,
        )
        with open(os.devnull, "w", encoding="utf-8") as stderr:
            async with stdio_client(parameters, errlog=stderr) as streams:
                yield streams
        return
    if target.transport == "sse":
        from mcp.client.sse import sse_client

        async with sse_client(
            target.url,
            headers=target.headers,
            timeout=timeout,
            sse_read_timeout=timeout,
        ) as streams:
            yield streams
        return
    if target.transport != "http":
        raise ValueError("unsupported MCP transport")

    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(
        headers=target.headers,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        async with streamable_http_client(
            target.url,
            http_client=client,
            max_sse_event_size=1024 * 1024,
        ) as streams:
            yield streams


async def _list_pages(
    method: Callable[..., Any], attribute: str
) -> tuple[list[Any], bool]:
    from mcp import types

    items: list[Any] = []
    cursor: str | None = None
    for _ in range(_MAX_PAGES):
        params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
        page = await method(params=params)
        items.extend(getattr(page, attribute))
        if len(items) >= _MAX_ITEMS:
            return items[:_MAX_ITEMS], True
        cursor = page.next_cursor
        if not cursor:
            return items, False
    return items, True


async def _discover_target(
    target: MCPDiscoveryTarget, timeout: float
) -> MCPAdvertisement:
    import anyio
    from mcp import ClientSession

    with anyio.fail_after(timeout):
        async with _connect(target, timeout) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timeout,
            ) as session:
                initialized = await session.initialize()
                tools: list[Any] = []
                resources: list[Any] = []
                templates: list[Any] = []
                prompts: list[Any] = []
                truncated = False
                errors: list[str] = []
                if initialized.capabilities.tools is not None:
                    try:
                        tools, hit_limit = await _list_pages(
                            session.list_tools, "tools"
                        )
                        truncated |= hit_limit
                    except Exception:
                        tools = []
                        errors.append("tools/list")
                if initialized.capabilities.resources is not None:
                    try:
                        resources, hit_limit = await _list_pages(
                            session.list_resources, "resources"
                        )
                        truncated |= hit_limit
                    except Exception:
                        resources = []
                        errors.append("resources/list")
                    try:
                        templates, hit_limit = await _list_pages(
                            session.list_resource_templates, "resource_templates"
                        )
                        truncated |= hit_limit
                    except Exception:
                        templates = []
                        errors.append("resources/templates/list")
                if initialized.capabilities.prompts is not None:
                    try:
                        prompts, hit_limit = await _list_pages(
                            session.list_prompts, "prompts"
                        )
                        truncated |= hit_limit
                    except Exception:
                        prompts = []
                        errors.append("prompts/list")
                return MCPAdvertisement(
                    protocol_version=initialized.protocol_version,
                    server_name=initialized.server_info.name,
                    server_version=initialized.server_info.version,
                    tools=tuple(item.model_dump(by_alias=True) for item in tools),
                    resources=tuple(
                        item.model_dump(by_alias=True) for item in resources
                    ),
                    resource_templates=tuple(
                        item.model_dump(by_alias=True) for item in templates
                    ),
                    prompts=tuple(item.model_dump(by_alias=True) for item in prompts),
                    truncated=truncated,
                    errors=tuple(errors),
                )


def _text(value: Any, sensitive_values: tuple[str, ...] = ()) -> str:
    """Bound untrusted metadata and remove known credentials before export."""
    text = _CONTROL_CHARACTERS.sub(" ", str(value or ""))
    for secret in sorted(set(sensitive_values), key=len, reverse=True):
        if secret:
            text = text.replace(secret, "[redacted]")
    text = _CREDENTIAL.sub(r"\1[redacted]", text)
    text = _INLINE_URL.sub(lambda match: _safe_uri(match.group()), text)
    return " ".join(text.split())[:_MAX_TEXT]


def _safe_uri(value: Any) -> str:
    # Do not call _text here: it sanitizes inline URLs with this helper.
    text = _CONTROL_CHARACTERS.sub("", str(value or ""))[:_MAX_TEXT]
    try:
        parsed = urlsplit(text)
        if not parsed.scheme:
            return text.split("?", 1)[0].split("#", 1)[0]
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit((parsed.scheme, host + port, parsed.path, "", ""))
    except ValueError:
        return ""


def _schema_summary(schema: Any, sensitive_values: tuple[str, ...] = ()) -> str:
    if not isinstance(schema, dict):
        return ""
    properties = schema.get("properties", {})
    summary: dict[str, Any] = {
        "type": _text(schema.get("type", "object"), sensitive_values)
    }
    if isinstance(properties, dict):
        summary["properties"] = {
            _text(name, sensitive_values): _text(
                value.get("type", "unknown"), sensitive_values
            )
            if isinstance(value, dict)
            else "unknown"
            for name, value in list(properties.items())[:50]
        }
    required = schema.get("required")
    if isinstance(required, list):
        summary["required"] = [_text(item, sensitive_values) for item in required[:50]]
    encoded = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    while len(encoded) > _MAX_TEXT and summary.get("properties"):
        summary["properties"].popitem()
        encoded = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    while len(encoded) > _MAX_TEXT and summary.get("required"):
        summary["required"].pop()
        encoded = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    return encoded if len(encoded) <= _MAX_TEXT else "{}"


def _annotations(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    return {
        name: value[name]
        for name in (
            "readOnlyHint",
            "destructiveHint",
            "idempotentHint",
            "openWorldHint",
        )
        if type(value.get(name)) is bool
    }


def _tool_enabled(server: Capability, name: str) -> bool:
    if name in server.tool_denylist:
        return False
    restricted = server.tool_allowlist_configured or bool(server.tool_allowlist)
    return not restricted or name in server.tool_allowlist


def _target_sensitive_values(target: MCPDiscoveryTarget) -> tuple[str, ...]:
    """Keep runtime credentials local; never put these values in node fields."""
    values = [*target.env.values(), *target.headers.values(), *target.args]
    values.extend(value for _, value in parse_qsl(urlsplit(target.url).query))
    for value in tuple(values):
        if "=" in value:
            values.append(value.partition("=")[2])
        if value.lower().startswith(("bearer ", "basic ")):
            values.append(value.partition(" ")[2])
    return tuple(value for value in values if value)


def _tool_dimensions(name: str, annotations: dict[str, Any]) -> dict[str, Any]:
    tokens = {item for item in _TOKEN_SPLIT.split(name.lower()) if item}
    fields: dict[str, Any] = {}
    evidence = f"advertised-tool:{name}"
    shell = bool(
        tokens & {"shell", "terminal", "exec", "execute", "process", "command"}
    )
    file_access = bool(tokens & {"file", "files", "filesystem", "directory", "path"})
    network = bool(
        tokens & {"fetch", "http", "request", "browser", "download", "upload"}
    )
    git_context = bool(tokens & {"git", "github", "repository", "repo"})
    write_action = bool(
        tokens & {"write", "create", "update", "delete", "commit", "push", "merge"}
    )
    if shell:
        fields.update(shell_exec="suspected", shell_evidence=evidence)
    if file_access:
        fields.update(
            file_access="suspected",
            file_evidence=evidence,
            file_readonly=bool(annotations.get("readOnlyHint")) or not write_action,
        )
    if network:
        fields.update(network_send="suspected", network_evidence=evidence)
    if (git_context and write_action) or name.lower() in {
        "create_or_update_file",
        "push_files",
        "create_pull_request",
    }:
        fields.update(git_write="suspected", git_evidence=evidence)
    return fields


def _advertisement_result(
    server: Capability,
    advertisement: MCPAdvertisement,
    discovered_at: str,
    sensitive_values: tuple[str, ...] = (),
) -> CollectionResult:
    result = CollectionResult()
    advertised_tools = advertisement.tools[:_MAX_ITEMS]
    names = tuple(
        item["name"]
        for item in advertised_tools
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]
    )
    profile = get_profile(server.profile_id) if server.profile_id else None
    # A complete tools/list constrains the current implementation claims. A
    # truncated/failed listing cannot establish that an unlisted tool is absent.
    complete_tools = (
        not advertisement.truncated and "tools/list" not in advertisement.errors
    )
    server_update: dict[str, Any] = {
        "discovery_status": "partial"
        if advertisement.errors or advertisement.truncated
        else "advertised",
        "discovery_error": _text(",".join(advertisement.errors), sensitive_values),
        "discovered_at": discovered_at,
        "protocol_version": _text(advertisement.protocol_version, sensitive_values),
        "server_implementation": _text(advertisement.server_name, sensitive_values),
        "server_version": _text(advertisement.server_version, sensitive_values),
        "advertised_tools": len(advertisement.tools),
        "advertised_tool_names": tuple(_text(name, sensitive_values) for name in names),
        "live_advertisement_confidence": "none"
        if "tools/list" in advertisement.errors
        else "confirmed",
        "advertised_resources": len(advertisement.resources)
        + len(advertisement.resource_templates),
        "advertised_prompts": len(advertisement.prompts),
        "discovery_truncated": advertisement.truncated,
    }
    edge_properties = {
        "confidence": "advertised",
        "evidence": "live MCP advertisement; execution and effective access unverified",
        "discovered_at": discovered_at,
    }
    live_claims = []
    seen_names: set[str] = set()
    for advertised in advertised_tools:
        if not isinstance(advertised, dict):
            continue
        raw_name = advertised.get("name")
        if not isinstance(raw_name, str) or not raw_name or raw_name in seen_names:
            continue
        seen_names.add(raw_name)
        if not _tool_enabled(server, raw_name):
            continue
        tool_name = _text(raw_name, sensitive_values)
        annotations = _annotations(advertised.get("annotations"))
        fields: dict[str, Any] = _tool_dimensions(tool_name, annotations)
        claims = ()
        if profile is not None:
            claims = tuple(
                claim.model_copy(
                    update={
                        "verification_status": "live_advertised",
                        "confidence": claim.confidence
                        if server.identity_confidence == "confirmed"
                        or claim.confidence == "none"
                        else "suspected",
                        "evidence": (
                            "Exact official tool currently advertised; "
                            if server.identity_confidence == "confirmed"
                            else "Tool name matches official schema and is currently advertised; "
                            "product identity unverified; "
                        )
                        + "execution and effective access unverified. "
                        + claim.evidence,
                    }
                )
                for claim in claims_for_profile(
                    profile,
                    server.tool_allowlist,
                    server.tool_denylist,
                    allowlist_configured=server.tool_allowlist_configured,
                    advertised_tools=(raw_name,),
                )
                if not annotations.get("readOnlyHint") or claim.readonly
            )
            # Known implementation tools are classified by verified exact tool
            # identities. Unverified tool names do not inherit static claims.
            fields = legacy_fields(claims)
        live_claims.extend(claim for claim in claims if claim not in live_claims)
        tool = Capability(
            name=f"{server.name}: {tool_name}",
            cap_kind="MCPTool",
            agent_scope=server.objectid,
            tool_name=tool_name,
            profile_id=server.profile_id,
            claims=claims,
            identity_confidence=server.identity_confidence,
            implementation_confidence=server.implementation_confidence
            if claims
            else "none",
            live_advertisement_confidence="confirmed",
            description=_text(advertised.get("description"), sensitive_values),
            input_schema=_schema_summary(
                advertised.get("inputSchema"), sensitive_values
            ),
            output_schema=_schema_summary(
                advertised.get("outputSchema"), sensitive_values
            ),
            read_only_hint=annotations.get("readOnlyHint"),
            destructive_hint=annotations.get("destructiveHint"),
            idempotent_hint=annotations.get("idempotentHint"),
            open_world_hint=annotations.get("openWorldHint"),
            discovery_status="advertised",
            discovered_at=discovered_at,
            **fields,
        )
        result.capabilities.append(tool)
        result.edges.append(
            Edge(
                start=server.objectid,
                end=tool.objectid,
                kind="Exposes",
                properties=edge_properties,
            )
        )
    if profile is not None and complete_tools:
        server_update.update(
            claims=tuple(live_claims),
            has_shell=False,
            shell_exec="none",
            file_access="none",
            network_send="none",
            git_write="none",
            file_readonly=False,
            shell_evidence="",
            file_evidence="",
            network_evidence="",
            git_evidence="",
        )
        server_update.update(legacy_fields(tuple(live_claims)))
        server_update["has_shell"] = server_update.get("shell_exec", "none") != "none"
    result.capabilities.insert(0, server.model_copy(update=server_update))
    for advertised in (*advertisement.resources, *advertisement.resource_templates)[
        :_MAX_ITEMS
    ]:
        if not isinstance(advertised, dict):
            continue
        uri = advertised.get("uri") or advertised.get("uriTemplate") or ""
        source = Source(
            name=f"{server.name}: {_text(advertised.get('title') or advertised.get('name'), sensitive_values)}",
            source_kind="MCPResource",
            path=_text(_safe_uri(uri), sensitive_values),
            is_external=True,
            description=_text(advertised.get("description"), sensitive_values),
            mime_type=_text(advertised.get("mimeType"), sensitive_values),
            mcp_server=server.name,
            discovery_status="advertised",
            influence_channel="mcp_advertisement",
            activation="observed",
            source_scope="session",
        )
        result.sources.append(source)
        result.edges.append(
            Edge(
                start=server.objectid,
                end=source.objectid,
                kind="Exposes",
                properties=edge_properties,
            )
        )
    for advertised in advertisement.prompts[:_MAX_ITEMS]:
        if not isinstance(advertised, dict):
            continue
        arguments = advertised.get("arguments") or []
        if not isinstance(arguments, list):
            arguments = []
        prompt_name = _text(advertised.get("name", ""), sensitive_values)
        source = Source(
            name=f"{server.name}: {_text(advertised.get('title') or prompt_name, sensitive_values)}",
            source_kind="MCPPrompt",
            path=f"mcp-prompt://{server.objectid}/{prompt_name}",
            is_external=True,
            description=_text(advertised.get("description"), sensitive_values),
            mcp_server=server.name,
            arguments=tuple(
                _text(item.get("name"), sensitive_values)
                for item in arguments[:50]
                if isinstance(item, dict)
            ),
            discovery_status="advertised",
            influence_channel="mcp_advertisement",
            activation="observed",
            source_scope="session",
        )
        result.sources.append(source)
        result.edges.append(
            Edge(
                start=server.objectid,
                end=source.objectid,
                kind="Exposes",
                properties=edge_properties,
            )
        )
    return result


def _error_name(exc: BaseException) -> str:
    nested = getattr(exc, "exceptions", ())
    if nested:
        return _error_name(nested[0])
    return type(exc).__name__


def collect_live_mcp(
    capabilities: list[Capability], workspace: Path, timeout: float = 10.0
) -> CollectionResult:
    """Discover advertised MCP metadata without invoking tools or reading content."""
    servers = [cap for cap in capabilities if cap.cap_kind == "MCPServer"]
    result = CollectionResult()
    if not servers:
        return result
    _require_sdk()

    import anyio

    for server in servers:
        discovered_at = datetime.now(timezone.utc).isoformat()
        try:
            target = _load_target(server, workspace)
            advertisement = anyio.run(_discover_target, target, timeout)
        except Exception as exc:
            result.capabilities.append(
                server.model_copy(
                    update={
                        "discovery_status": "failed",
                        "discovery_error": _error_name(exc),
                        "discovered_at": discovered_at,
                    }
                )
            )
            continue
        partial = _advertisement_result(
            server, advertisement, discovered_at, _target_sensitive_values(target)
        )
        result.capabilities.extend(partial.capabilities)
        result.sources.extend(partial.sources)
        result.edges.extend(partial.edges)
    return result
