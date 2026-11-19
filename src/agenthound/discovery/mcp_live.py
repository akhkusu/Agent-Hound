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
from urllib.parse import urlsplit, urlunsplit

from agenthound.collectors.base import CollectionResult
from agenthound.models.edges import Edge
from agenthound.models.nodes import Capability, Source

_MAX_ITEMS = 200
_MAX_PAGES = 10
_MAX_TEXT = 1000
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


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


def _find_definition(raw: dict[str, Any], server: Capability, workspace: Path) -> dict[str, Any]:
    if server.mcp_scope == "local":
        projects = raw.get("projects", {})
        project = projects.get(str(workspace.resolve()), {}) if isinstance(projects, dict) else {}
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
async def _connect(target: MCPDiscoveryTarget, timeout: float) -> AsyncIterator[tuple[Any, Any]]:
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


async def _discover_target(target: MCPDiscoveryTarget, timeout: float) -> MCPAdvertisement:
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
                        tools, hit_limit = await _list_pages(session.list_tools, "tools")
                        truncated |= hit_limit
                    except Exception:
                        tools = []
                        errors.append("tools/list")
                if initialized.capabilities.resources is not None:
                    try:
                        resources, hit_limit = await _list_pages(session.list_resources, "resources")
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
                        prompts, hit_limit = await _list_pages(session.list_prompts, "prompts")
                        truncated |= hit_limit
                    except Exception:
                        prompts = []
                        errors.append("prompts/list")
                return MCPAdvertisement(
                    protocol_version=initialized.protocol_version,
                    server_name=initialized.server_info.name,
                    server_version=initialized.server_info.version,
                    tools=tuple(item.model_dump(by_alias=True) for item in tools),
                    resources=tuple(item.model_dump(by_alias=True) for item in resources),
                    resource_templates=tuple(item.model_dump(by_alias=True) for item in templates),
                    prompts=tuple(item.model_dump(by_alias=True) for item in prompts),
                    truncated=truncated,
                    errors=tuple(errors),
                )


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())[:_MAX_TEXT]


def _safe_uri(value: Any) -> str:
    text = _text(value)
    parsed = urlsplit(text)
    if not parsed.scheme:
        return text
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    netloc = host + port if host else parsed.netloc.split("@")[-1]
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _schema_summary(schema: Any) -> str:
    if not isinstance(schema, dict):
        return ""
    properties = schema.get("properties", {})
    summary: dict[str, Any] = {"type": schema.get("type", "object")}
    if isinstance(properties, dict):
        summary["properties"] = {
            str(name): value.get("type", "unknown") if isinstance(value, dict) else "unknown"
            for name, value in list(properties.items())[:50]
        }
    required = schema.get("required")
    if isinstance(required, list):
        summary["required"] = [str(item) for item in required[:50]]
    return json.dumps(summary, sort_keys=True, separators=(",", ":"))


def _tool_dimensions(name: str, annotations: dict[str, Any]) -> dict[str, Any]:
    tokens = {item for item in _TOKEN_SPLIT.split(name.lower()) if item}
    fields: dict[str, Any] = {}
    evidence = f"advertised-tool:{name}"
    shell = bool(tokens & {"shell", "terminal", "exec", "execute", "process", "command"})
    file_access = bool(tokens & {"file", "files", "filesystem", "directory", "path"})
    network = bool(tokens & {"fetch", "http", "request", "browser", "download", "upload"})
    git_context = bool(tokens & {"git", "github", "repository", "repo"})
    write_action = bool(tokens & {"write", "create", "update", "delete", "commit", "push", "merge"})
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
        "create_or_update_file", "push_files", "create_pull_request"
    }:
        fields.update(git_write="suspected", git_evidence=evidence)
    return fields


def _advertisement_result(
    server: Capability, advertisement: MCPAdvertisement, discovered_at: str
) -> CollectionResult:
    result = CollectionResult()
    result.capabilities.append(server.model_copy(update={
        "discovery_status": "partial" if advertisement.errors else "advertised",
        "discovery_error": ",".join(advertisement.errors),
        "discovered_at": discovered_at,
        "protocol_version": advertisement.protocol_version,
        "server_implementation": advertisement.server_name,
        "server_version": advertisement.server_version,
        "advertised_tools": len(advertisement.tools),
        "advertised_resources": len(advertisement.resources) + len(advertisement.resource_templates),
        "advertised_prompts": len(advertisement.prompts),
        "discovery_truncated": advertisement.truncated,
    }))
    edge_properties = {
        "confidence": "advertised",
        "evidence": "live MCP discovery; content was not invoked or read",
        "discovered_at": discovered_at,
    }
    for advertised in advertisement.tools:
        annotations = advertised.get("annotations") or {}
        tool = Capability(
            name=f"{server.name}: {advertised.get('title') or advertised.get('name')}",
            cap_kind="MCPTool",
            agent_scope=server.objectid,
            description=_text(advertised.get("description")),
            input_schema=_schema_summary(advertised.get("inputSchema")),
            output_schema=_schema_summary(advertised.get("outputSchema")),
            read_only_hint=annotations.get("readOnlyHint"),
            destructive_hint=annotations.get("destructiveHint"),
            idempotent_hint=annotations.get("idempotentHint"),
            open_world_hint=annotations.get("openWorldHint"),
            discovery_status="advertised",
            discovered_at=discovered_at,
            **_tool_dimensions(str(advertised.get("name", "")), annotations),
        )
        result.capabilities.append(tool)
        result.edges.append(Edge(
            start=server.objectid,
            end=tool.objectid,
            kind="Exposes",
            properties=edge_properties,
        ))
    for advertised in (*advertisement.resources, *advertisement.resource_templates):
        uri = advertised.get("uri") or advertised.get("uriTemplate") or ""
        source = Source(
            name=f"{server.name}: {advertised.get('title') or advertised.get('name')}",
            source_kind="MCPResource",
            path=_safe_uri(uri),
            is_external=True,
            description=_text(advertised.get("description")),
            mime_type=_text(advertised.get("mimeType")),
            mcp_server=server.name,
            discovery_status="advertised",
        )
        result.sources.append(source)
        result.edges.append(Edge(
            start=server.objectid,
            end=source.objectid,
            kind="Exposes",
            properties=edge_properties,
        ))
    for advertised in advertisement.prompts:
        arguments = advertised.get("arguments") or []
        source = Source(
            name=f"{server.name}: {advertised.get('title') or advertised.get('name')}",
            source_kind="MCPPrompt",
            path=f"mcp-prompt://{server.objectid}/{advertised.get('name', '')}",
            is_external=True,
            description=_text(advertised.get("description")),
            mcp_server=server.name,
            arguments=tuple(str(item.get("name")) for item in arguments if isinstance(item, dict)),
            discovery_status="advertised",
        )
        result.sources.append(source)
        result.edges.append(Edge(
            start=server.objectid,
            end=source.objectid,
            kind="Exposes",
            properties=edge_properties,
        ))
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
            result.capabilities.append(server.model_copy(update={
                "discovery_status": "failed",
                "discovery_error": _error_name(exc),
                "discovered_at": discovered_at,
            }))
            continue
        partial = _advertisement_result(server, advertisement, discovered_at)
        result.capabilities.extend(partial.capabilities)
        result.sources.extend(partial.sources)
        result.edges.extend(partial.edges)
    return result
