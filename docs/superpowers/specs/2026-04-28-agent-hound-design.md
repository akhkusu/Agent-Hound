# Agent-Hound Design Spec
_2026-04-28_

## Overview

Agent-Hound is a BloodHound OpenGraph collector that maps the transition of untrusted data into untrusted actions in AI agent environments. It models Indirect Prompt Injection (IPI) attack paths using a four-pillar graph: **Source → Agent → Capability → Asset/Impact**.

Import the output into BloodHound CE 8.0+ and visualize how a poisoned README can lead to your SSH key being exfiltrated.

---

## Stack

- Python 3.10+
- Click (CLI), Pydantic v2 (models), Rich (terminal output)
- BloodHound OpenGraph v6 JSON output
- Same structure as MCP-Hound (the reference implementation)

---

## Project Structure

```
Agent-Hound/
├── logo.png
├── README.md
├── CHANGELOG.md
├── .gitignore
├── pyproject.toml
├── query.md
├── src/
│   └── agenthound/
│       ├── __init__.py
│       ├── cli.py
│       ├── platforms.py
│       ├── discovery/
│       │   ├── __init__.py
│       │   ├── config_parser.py
│       │   └── workspace.py
│       ├── collectors/
│       │   ├── __init__.py
│       │   ├── capabilities.py
│       │   ├── assets.py
│       │   ├── sources.py
│       │   └── impact.py
│       ├── models/
│       │   ├── __init__.py
│       │   ├── nodes.py
│       │   └── edges.py
│       └── output/
│           ├── __init__.py
│           └── opengraph.py
├── tests/
│   ├── __init__.py
│   ├── fixtures/
│   │   ├── claude_desktop_config.json
│   │   ├── claude_code_settings.json
│   │   ├── vscode_mcp.json
│   │   └── cursor_mcp.json
│   ├── jsonschema/
│   │   ├── node.json
│   │   └── edge.json
│   ├── test_config_parser.py
│   └── test_opengraph.py
└── examples/
    └── sample_output.json
```

---

## Node Model

Five node types. Object IDs are `{prefix}-{sha256[:8]}` of key fields. Names stored lowercase internally, uppercased in OpenGraph output.

| Kind | ID Prefix | Key Properties | BH Icon | Color |
|------|-----------|---------------|---------|-------|
| `Agent` | `agent-` | name, platform, config_path | `robot` | `#e74c3c` |
| `Source` | `source-` | name, source_kind, path, is_external | `file-import` | `#f39c12` |
| `Capability` | `cap-` | name, cap_kind, command, transport, has_shell | `bolt` | `#9b59b6` |
| `Asset` | `asset-` | name, asset_kind, path, readable, writable | `lock` | `#e67e22` |
| `Impact` | `impact-` | name, impact_kind, reachable, description | `skull` | `#c0392b` |

### Source kinds
`WebPage`, `GitHubIssue`, `DocFile`, `SlackThread`, `Skill`, `AgentInstruction`

### Capability kinds
`MCPTool`, `MCPServer`, `ShellHook`, `Permission`

### Asset kinds
`SSHKey`, `EnvFile`, `AwsCredentials`, `GCloudCredentials`, `KubeConfig`, `SourceCode`

### Impact kinds
`Exfiltration`, `SupplyChainContamination`, `SystemTakeover`

---

## Edge Model

| Kind | From | To | Meaning |
|------|------|----|---------|
| `Influences` | Source | Agent | External data is an IPI vector into agent reasoning |
| `HasCapability` | Agent | Capability | Agent has this tool/server/hook enabled |
| `CanAccess` | Capability | Asset | Tool can read or write this sensitive file/credential |
| `Triggers` | Capability | Impact | Tool can produce this irreversible external consequence |

---

## Collectors

### `capabilities.py` — Configuration Scan

Parses agent config files. Produces one `Agent` node per config + one `Capability` node per tool/MCP server/hook. Creates `HasCapability` edges.

Supported configs (auto-discovered by `discover` command):

| Agent | Config Path |
|-------|-------------|
| Claude Desktop | `~/.config/Claude/claude_desktop_config.json` (Linux/macOS), `%APPDATA%\Claude\...` (Windows) |
| Claude Code | `~/.claude/settings.json` |
| VS Code Copilot | `~/.vscode/mcp.json` |
| Cursor | `~/.cursor/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |

### `sources.py` — Environment Scan

Walks the workspace for IPI-injectable files. Produces `Source` nodes + `Influences` edges to the Agent.

Flags as Sources:
- `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `COPILOT-INSTRUCTIONS.md` (agent instruction files)
- `*.md`, `*.txt`, `*.html` files in workspace (potential web-fetched content)
- Files in directories named `docs/`, `context/`, `prompts/`, `instructions/`
- Any file with `http` URLs in content (fetched external data)

### `assets.py` — Permission Scan

Walks home directory and workspace for high-value targets. Produces `Asset` nodes + `CanAccess` edges from Capabilities that have shell or filesystem access.

Scans for:
- SSH keys: `~/.ssh/id_*`, `~/.ssh/*.pem`, `~/.ssh/*.key`
- Env files: `.env`, `.env.*`, `*.env`, `*.env.local`
- Cloud credentials: `~/.aws/credentials`, `~/.config/gcloud/application_default_credentials.json`, `~/.kube/config`
- Source code: git repositories (detected by `.git/` presence)

`CanAccess` edges are drawn from Capabilities whose `cap_kind` is `MCPServer` (filesystem/shell MCP servers) or `ShellHook`, or whose `has_shell` flag is `True`. Read-only capabilities do not get `CanAccess` edges to writable Assets.

### `impact.py` — Connectivity Scan

Tests what irreversible external actions are possible. Produces `Impact` nodes + `Triggers` edges from relevant Capabilities.

- **Exfiltration**: HTTP/HTTPS reachable (tests connectivity) + any `fetch_url`/`curl`/network capability exists
- **SupplyChain**: `git push` capability + remote URL detected in any repo
- **SystemTakeover**: Shell execution capability (`execute_shell`, `run_command`, `bash`) exists

---

## CLI

Package: `agenthound` · Command: `agenthound`

```
agenthound [--config PATH] [--workspace PATH] [--scope all|workspace|global]
           [--output PATH] [--verbose]

agenthound discover [--workspace PATH] [--scope all|workspace|global]
                    [--output PATH] [--verbose]

agenthound register-icons [--bh-url URL] [--username USER] [--password PASS]
```

`--scope` controls what gets scanned:
- `workspace` — Sources from workspace only, Assets from workspace only
- `global` — Sources skipped, Assets from home dir only
- `all` (default) — Sources from workspace, Assets from home dir + workspace

On `--verbose`, prints a **Scope Summary** table before scanning so users see exactly what paths will be touched.

---

## OpenGraph Output

```json
{
  "metadata": { "source_kind": "AgentHound" },
  "custom_types": {
    "Agent":      { "icon": { "name": "robot",       "type": "font-awesome", "color": "#e74c3c" } },
    "Source":     { "icon": { "name": "file-import",  "type": "font-awesome", "color": "#f39c12" } },
    "Capability": { "icon": { "name": "bolt",         "type": "font-awesome", "color": "#9b59b6" } },
    "Asset":      { "icon": { "name": "lock",         "type": "font-awesome", "color": "#e67e22" } },
    "Impact":     { "icon": { "name": "skull",        "type": "font-awesome", "color": "#c0392b" } }
  },
  "graph": {
    "nodes": [ ... ],
    "edges": [ ... ]
  }
}
```

---

## Supporting Files

### `.gitignore`
Python standard (`__pycache__`, `*.pyc`, `.venv`, `dist/`, `*.egg-info`) plus:
```
# Claude Code generated
CLAUDE.md
docs/superpowers/
.claude/
*.claude.json
```

### `CHANGELOG.md`
Keep a Changelog format, starting at `v0.1.0 - 2026-04-28`.

### `README.md`
Simple: logo, one-liner, install, usage with `--scope` flag, node/edge table, three example Cypher queries.

### `query.md`
Full Cypher reference covering:
- All nodes/edges by pillar
- IPI source chains (Source → Agent → Capability → Asset)
- Exfiltration paths (Capability → Impact)
- Highest-risk paths (Source → Impact via shell)
- Risk scoring query

---

## Key Example Cypher Queries

```cypher
-- Full IPI attack chain
MATCH path = (s:Source)-[:Influences]->(a:Agent)-[:HasCapability]->(c:Capability)-[:CanAccess]->(asset:Asset)
RETURN path

-- Exfiltration paths
MATCH path = (c:Capability)-[:Triggers]->(i:Impact {impact_kind: "Exfiltration"})
RETURN path

-- Choke point: which Capability appears in most Impact paths?
MATCH (c:Capability)-[:Triggers]->(i:Impact)
RETURN c.name, count(i) AS impact_count
ORDER BY impact_count DESC
```

---

## Dependencies

```toml
[project]
dependencies = [
    "click>=8.0",
    "pydantic>=2.0",
    "rich>=13.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov",
    "ruff",
    "mypy",
]
```

---

## Phase Roadmap

- **Phase 1** (this spec): Config parsing, four-pillar node/edge generation, OpenGraph output, CLI with `--scope`
- **Phase 2**: Runtime tool enumeration via MCP protocol, OAuth token discovery, NeighborJack detection (0.0.0.0 binding)
- **Phase 3**: Risk scoring per chain, Cypher query templates embedded in CLI, SARIF output option
