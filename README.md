# Agent-Hound 

<p align="center">
  <img src="img/logo.png" width="300" alt="Agent-Hound Logo"/>
</p>

<p align="center"><strong>Discover Attack Paths in AI Agent Environments.</strong></p>

<p align="center">
  <a href="https://bloodhound.specterops.io/opengraph/overview"><img src="https://img.shields.io/badge/BloodHound-OpenGraph-E31B23" alt="BloodHound OpenGraph"/></a>
  <a href="#graph-model"><img src="https://img.shields.io/badge/AI-Agent%20Security-6f42c1" alt="AI Agent Security"/></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.10+"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"/></a>
</p>

**Agent-Hound** maps potential **Indirect Prompt Injection (IPI)** attack paths in AI agent environments such as Claude Code and Codex. It builds a graph from agent configuration, local source files, sensitive file locations, and connectivity checks so you can inspect where untrusted inputs meet privileged capabilities.

Built on the [SpecterOps OpenGraph](https://specterops.io/opengraph/) specification, Agent-Hound produces JSON for [BloodHound CE](https://bloodhound.specterops.io/) File Ingest, letting you visualize potential attack paths and query them with Cypher.

---

## Graph Model

Agent-Hound models AI agent environments as five node types connected by four edge types, then exports the graph to BloodHound OpenGraph:

<p align="center">
  <img src="img/demo.png" alt="Agent-Hound graph in BloodHound" width="100%"/>
</p>

| Node | Role | Examples |
| :--- | :--- | :--- |
| **Source** | Local files that could influence an agent | `README.md`, `CLAUDE.md`, `AGENTS.md` |
| **Agent** | Agent environment or observed API session | Claude Code, Codex, Copilot |
| **Capability** | Configured MCP servers, command hooks, and supported built-in tools | `filesystem`, `github`, `Read`, `Bash` |
| **Asset** | Discovered sensitive files and source repositories | `~/.ssh/id_rsa`, `.env`, a local Git repository |
| **Impact** | Potential consequences inferred from capabilities and environment checks | `System-Takeover`, `Internet-Exfiltration`, `Supply-Chain-Contamination` |

**Edges:** `Influences` (Source → Agent) · `HasCapability` (Agent → Capability) · `CanAccess` (Capability → Asset) · `Triggers` (Capability → Impact)

---


## Usage

### Installation

Requires **Python 3.10+**. From the root of a local checkout:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
agenthound --help
```

### Scan a Project

```bash
cd /path/to/project
agenthound discover -o output.json -v
```

`discover` reads supported home/project configuration and includes local Windows Copilot observations when `LOCALAPPDATA` is available. It writes one graph and prints an **Attack Paths** preview with agent, capability, document name, confidence and context requirements. Use `--no-copilot` to skip local Copilot inventory, or `--local-app-data` for an authorized offline copy. For options, run `agenthound discover --help`.

### Import into BloodHound

Open **BloodHound CE** (v8.0+), go to **File Ingest**, and upload the generated OpenGraph JSON. Explore the graph using Search or Cypher.

See [query.md](query.md) for example Cypher queries. For a graph preview without a live scan, see the [sample output](examples/sample_output.json) and [demo graph](examples/demo.json).

### Windows Copilot and Microsoft 365

Use the same entry point for local agents and SharePoint:

```powershell
# One graph: local agents plus live SharePoint file metadata using Copilot WAM
agenthound discover --m365 -o combined_graph.json

# One graph: local agents plus an existing evidence-backed M365 snapshot
agenthound discover --m365-snapshot examples/m365_snapshot.json -o combined_graph.json

# Enumerate discovered SharePoint locations fully and retain up to 100,000 files
agenthound discover --scope all --m365 --enumerate-all --limit 100000 -o .local/environment_graph.json
```

`--m365` requires the Windows authentication dependencies and an existing authorized Copilot account. It retains up to 20 file metadata records by default (`--limit` changes this). Each document has its own node with its original filename. The graph uses the existing `Agent`, `Capability`, `Asset` and `Source` kinds. ACL identities and group membership are inputs to reachability analysis, not additional displayed node/edge kinds.

Normal M365 collection targets visible document and site-content libraries, such as Documents and Site Pages. Hidden libraries, `_catalogs`, form templates, converted forms, Style Library and root `Forms` folders are excluded. Library names are not required to be English, and file extensions are not used as an exclusion rule.

Documents are both `Source` and `Asset` nodes. Each has a conditional `Influences → Copilot → HasCapability → document read → CanAccess → document` route, so the **Full Attack Chain** query also returns Copilot candidates. These edges are `suspected`, require explicit document context, and retain `retrieval_verified: false`: metadata listing proves account-level discovery, not actual model ingestion. `metadata_access_verified` preserves the API observation. Snapshot references can provide stronger per-document retrieval evidence. See [the integration guide](docs/m365-integration.md).

Metadata-only observations also carry `content_access_verification: not_tested`. A false `content_access_verified` means document download has not been verified; it does not establish denial. A separately verified REST download proves API content access for that document, while Copilot inference retrieval remains a separate observation.

Codex collection includes a suspected workspace read capability even when no MCP server is configured. A shell candidate requires an explicit write-capable sandbox configuration and is suppressed when its shell tool is disabled. Runtime trust, managed policy and CLI overrides remain unverified. See [OpenAI's approvals and security documentation](https://learn.chatgpt.com/docs/agent-approvals-security).

The focused `copilot-windows`, `m365-import` and `m365-files` commands remain available for inventory or troubleshooting. Generic OpenGraph output can be displayed with the supplied Cypher queries; standard BloodHound Pathfinding requires a separately installed extension definition schema, which this repository does not yet provide. See [the OpenGraph graph model documentation](https://bloodhound.specterops.io/opengraph/developer/graph-data).

`agenthound m365-files` uses the existing Windows Copilot account to discover SharePoint locations and display up to 20 file metadata records by default, without a new app registration or external authentication CLI. Counts distinguish library-reported items, enumerated metadata and unverified content access. See [usage](docs/m365-files.md) and [how authentication, discovery and counts work](docs/copilot-sharepoint-auth.md).

Machine-specific lab scripts, live evidence, chat transcripts and collected inventory belong under `.local/` (ignored by Git and workspace scanning). Public examples and tests must use synthetic data. See [the contribution boundary](docs/local-data.md).

## Connecting to an AD Graph

Connect Agent-Hound's OpenGraph data to an AD graph in BloodHound and trace paths from AI agent environments to AD users, computers, and groups. See the [AD integration guide](docs/ad-integration.md).

<p align="center">
  <img src="img/ad-pathfinding-poc.png" alt="PoC BloodHound path from README.md through Claude Code to an AD group" width="100%"/>
</p>

---

## Disclaimer

**For Authorized Security Testing Only.**

Agent-Hound is designed to assist security professionals and developers in identifying potential risks in AI agent configurations. The creator assumes no liability for any misuse of this tool. Mapping an attack path does not guarantee an exploit is possible, nor does the absence of a path guarantee absolute security. Use with caution in production environments.

---

## License

MIT

---
