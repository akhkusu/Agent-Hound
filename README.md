# Agent-Hound

<p align="center">
  <img src="img/logo.png" width="300" alt="Agent-Hound"/>
</p>

**BloodHound OpenGraph collector for AI agent attack paths.**

Agent-Hound maps how untrusted data becomes untrusted actions — visualizing Indirect Prompt Injection (IPI) chains as BloodHound attack paths.

```
$ agenthound --config ~/.config/Claude/claude_desktop_config.json --scope all -o output.json -v

Agent-Hound v0.1.0

         Scope Summary
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Pillar        ┃ Scanning                  ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Capabilities  │ claude_desktop_config.json│
│ Sources       │ /srv/myproject            │
│ Assets        │ home dir + /srv/myproject │
│ Impact        │ connectivity + shell + git│
└───────────────┴───────────────────────────┘

Output written to output.json
  Nodes: 14 | Edges: 22

Security Findings:
  3 IPI source(s): README.md, CLAUDE.md, notes.txt
  2 asset(s) reachable via shell capability
  Exfiltration path detected: internet is reachable
```

## Installation

```bash
pip install .
```

For development:

```bash
pip install -e ".[dev]"
```

Requires Python 3.10+.

## Usage

```bash
# Scan a specific config
agenthound --config ~/.config/Claude/claude_desktop_config.json --output output.json

# Auto-discover all agent configs
agenthound discover --output output.json

# Restrict scope (workspace | global | all)
agenthound --config config.json --scope workspace --output output.json

# Verbose with scope summary
agenthound --config config.json --scope all --verbose
```

## The Four Pillars

| Node | Description | Icon |
|------|-------------|------|
| **Agent** | The AI agent (Claude, Cursor, etc.) | robot |
| **Source** | External data that can contain IPI | file-import |
| **Capability** | Tools and MCP servers the agent can use | bolt |
| **Asset** | Sensitive files and credentials | lock |
| **Impact** | Irreversible external consequences | skull |

Edges: `Influences` · `HasCapability` · `CanAccess` · `Triggers`

## Importing into BloodHound

1. Start BloodHound CE 8.0+
2. Go to **File Ingest**
3. Upload the generated JSON file
4. Query with Cypher (see `query.md`)

<p align="center">
  <img src="img/demo.png" alt="Agent-Hound graph in BloodHound"/>
</p>

## License

MIT
