"""Extract registered Claude MCP definitions without exporting app/auth state."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agenthound.models.nodes import Agent, Capability


def claude_mcp_paths(workspace: Path) -> list[Path]:
    user = (Path(os.environ['CLAUDE_CONFIG_DIR']).expanduser() / '.claude.json'
            if os.environ.get('CLAUDE_CONFIG_DIR') else Path.home() / '.claude.json')
    return [user, workspace / '.mcp.json']


def load_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f'Cannot parse config {path}: {exc}') from exc
    if not isinstance(raw, dict):
        raise ValueError(f'Config must be an object: {path}')
    return raw


def mcp_capabilities(agent: Agent, definitions: dict[str, tuple[dict[str, Any], Path, str]]) -> list[Capability]:
    from agenthound.discovery.config_parser import _build_mcp_capability
    result = []
    for name, (definition, path, scope) in definitions.items():
        cap = _build_mcp_capability(name, definition, agent.objectid)
        unresolved = any('${' in str(v) for v in [definition.get('command', ''), *definition.get('args', [])])
        fields: dict[str, Any] = {
            # Raw command arguments may hold credentials. Keep classification
            # evidence, but never export commands/args/env/headers/app state.
            'command': None, 'mcp_config_path': str(path), 'mcp_scope': scope,
            'transport': definition.get('type', cap.transport),
        }
        for dimension in ('file_access', 'shell_exec', 'network_send', 'git_write'):
            fields[dimension] = 'none' if unresolved else ('suspected' if getattr(cap, dimension) != 'none' else 'none')
        fields['has_shell'] = fields['shell_exec'] != 'none'
        fields['mcp_evidence'] = f'config:{path}; scope:{scope}; registered definition; runtime enablement, approval, trust, managed policy and connection unverified'
        if unresolved:
            fields['mcp_evidence'] += '; environment placeholders unresolved; no derived access/impact inferred'
        for key in ('file_evidence', 'shell_evidence', 'network_evidence', 'git_evidence'):
            if getattr(cap, key):
                fields[key] = getattr(cap, key) + '; ' + fields['mcp_evidence']
        result.append(cap.model_copy(update=fields))
    return result


def collect_mcp(paths: list[Path], workspace: Path, agent: Agent, settings: dict[str, Any]) -> tuple[list[Capability], set[str]]:
    supplied = {p.resolve() for p in paths}
    user_path, project_path = claude_mcp_paths(workspace)
    definitions: dict[str, tuple[dict[str, Any], Path, str]] = {}
    state: dict[str, Any] = {}
    local: dict[str, Any] = {}
    if user_path.resolve() in supplied:
        state = load_object(user_path)
        projects = state.get('projects', {})
        if isinstance(projects, dict):
            # Exact workspace only; never union other projects' private servers.
            local = projects.get(str(workspace.resolve()), {})
            if not isinstance(local, dict):
                local = {}
        for name, server in state.get('mcpServers', {}).items():
            definitions[name] = (server, user_path, 'user')
    if project_path.resolve() in supplied:
        for name, server in load_object(project_path).get('mcpServers', {}).items():
            definitions[name] = (server, project_path, 'project')
    for name, server in local.get('mcpServers', {}).items():
        definitions[name] = (server, user_path, 'local')
    registered_names = set(definitions)
    denied = set(local.get('disabledMcpServers', []))
    denied_project = set(local.get('disabledMcpjsonServers', [])) | set(settings.get('disabledMcpjsonServers', []))
    definitions = {name: item for name, item in definitions.items()
                   if name not in denied and not (item[2] == 'project' and name in denied_project)}
    if not all(isinstance(server, dict) for server, _, _ in definitions.values()):
        raise ValueError('MCP server definitions must be objects')
    return mcp_capabilities(agent, definitions), registered_names
