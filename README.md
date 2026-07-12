# Agent-Hound 

<p align="center">
  <img src="img/logo.png" width="300" alt="Agent-Hound Logo"/>
</p>

**Discover the Attack Paths Created by AI Agents.**

**Agent-Hound** is a security tool that maps the attack paths within AI agent environments like Claude Code and Claude Desktop. By identifying the intersection of untrusted data sources and privileged capabilities, it reveals the paths that **Indirect Prompt Injection (IPI)** attacks can exploit.

Built on the [SpecterOps OpenGraph](https://specterops.io/opengraph/) specification, Agent-Hound produces a JSON file that [BloodHound](https://bloodhound.specterops.io/) can ingest, letting you visualize attack paths as a graph.

---

## The Concept: Source-to-Impact

An AI agent working in your project reads a lot: `README.md`, `CLAUDE.md`, `AGENTS.md`, open GitHub issues, runbooks. Any of these can carry a hidden instruction planted by an attacker — this is **Indirect Prompt Injection**. On its own, that's just text. The danger is what the agent can *do* from there.

That same agent may have shell access, can push to git, can make outbound HTTP requests, and can read files like `~/.ssh/id_rsa` or `.env`. A poisoned source becomes the first step of a full attack chain — ending in data exfiltration, system takeover, or supply chain contamination.

Agent-Hound models this chain as four interconnected node types, producing a graph you can explore in BloodHound:

<p align="center">
  <img src="img/demo.png" alt="Agent-Hound graph in BloodHound" width="100%"/>
</p>

| Node | Role | Examples |
| :--- | :--- | :--- |
| **Source** | Untrusted inputs the agent reads | `README.md`, `CLAUDE.md`, `GitHub Issues` |
| **Capability** | Tools and MCP servers the agent can invoke | `BASH-HOOK`, `GITHUB`, `REMOTE-API` |
| **Asset** | Sensitive local data reachable via those tools | `~/.ssh/id_rsa`, `.env`, `KubeConfig` |
| **Impact** | Irreversible real-world consequences | `System-Takeover`, `Internet-Exfiltration`, `Supply-Chain-Contamination` |

**Edges:** `Influences` (Source → Agent) · `HasCapability` (Agent → Capability) · `CanAccess` (Capability → Asset) · `Triggers` (Capability → Impact)

### Facts vs. guesses

BloodHound is built to show verified access facts, so Agent-Hound is explicit about how sure it is. Every derived `CanAccess` / `Triggers` edge carries two properties you can query in BloodHound:

* `confidence` — `confirmed` when the claim comes from a fact in the config (a known MCP package such as `@modelcontextprotocol/server-filesystem`, a shell binary, a hook definition), `suspected` when it comes from a name-based heuristic only.
* `evidence` — what produced the edge, e.g. `package:@modelcontextprotocol/server-github (push_files / create_or_update_file via GitHub API)` or `keyword:file`.

Known packages are matched against a verified capability registry (`src/agenthound/discovery/profiles.py`), and `server-filesystem`-style allowed directories restrict `CanAccess` to assets inside those directories.

```cypher
MATCH p=()-[r:CanAccess|Triggers]->() WHERE r.confidence = 'confirmed' RETURN p
```

---

## Defending Against Real-World Threats

Agent-Hound identifies the exact attack paths seen in recent high-profile AI vulnerabilities:

### RCE via Malicious Rules (CVE-2025-54135)
* **The Threat:** Malicious `.cursorrules` or instructions in a repo tricking an agent into executing shell commands.
* **Hound Path:** `Source: README.md` ⮕ `Capability: BASH-HOOK` ⮕ `Impact: SYSTEM-TAKEOVER`.
* **Detection:** Flags agents with auto-execution capabilities in untrusted workspaces.

### Silent Data Exfiltration (EchoLeak)
* **The Threat:** An IPI forcing the agent to leak secrets via a hidden web request.
* **Hound Path:** `Source: GITHUB-ISSUE` ⮕ `Capability: REMOTE-API` ⮕ `Asset: .ENV` ⮕ `Impact: INTERNET-EXFILTRATION`.
* **Detection:** Identifies the "Exfiltration Loop" where an agent has a network-capable tool and the host has internet connectivity.

### Supply Chain Contamination
* **The Threat:** AI agents writing backdoored code because they trusted a malicious project doc.
* **Hound Path:** `Source: CLAUDE.md` ⮕ `Capability: GITHUB` ⮕ `Impact: SUPPLY-CHAIN-CONTAMINATION`.
* **Detection:** Flags risks where an agent has write access to production repositories and is influenced by untrusted documentation.

---

## Usage

### Installation
```bash
pip install agenthound
```

### CLI Summary
Agent-Hound scans your environment and generates an OpenGraph-compliant JSON file.

```text
$ agenthound --config ~/.config/Claude/claude_desktop_config.json --scope all -o output.json -v

Agent-Hound v0.1.0

        Scope Summary
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Pillar        ┃ Scanning                 ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Capabilities  │ claude_desktop_config.json│
│ Sources       │ /srv/myproject           │
│ Assets        │ home dir + /srv/myproject │
│ Impact        │ connectivity=reachable + shell + git│
└───────────────┴───────────────────────────┘

Output written to output.json
  Nodes: 9 | Edges: 11

Security Findings:
  3 IPI source(s): README.md, CLAUDE.md, notes.txt
  2 asset(s) reachable via shell capability
  Exfiltration path detected: internet is reachable
```

### Importing to BloodHound
1. Run Agent-Hound to generate `output.json`.
2. Open **BloodHound CE** (v8.0+).
3. Use **File Ingest** to upload the JSON.
4. Run Cypher queries to find paths:
   ```cypher
   MATCH p=(s:Source)-[*]->(i:Impact) RETURN p
   ```

---

## Disclaimer

**For Authorized Security Testing Only.**

Agent-Hound is designed to assist security professionals and developers in identifying potential risks in AI agent configurations. The creator assumes no liability for any misuse of this tool. Mapping an attack path does not guarantee an exploit is possible, nor does the absence of a path guarantee absolute security. Use with caution in production environments.

---

## License

MIT

---

