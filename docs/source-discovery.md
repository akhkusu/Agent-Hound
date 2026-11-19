# Behavior source discovery

Coverage reviewed against official documentation on **2026-11-19**.

Agent-Hound discovers documented agent files, bounded local references from those files, and the existing scoped document sources. It does not treat every readable file as agent context. Ordinary discovery uses the normal scan command:

```bash
agenthound scan /path/to/project --scope all -o graph.json -v
```

`-w /path/to/project` is an alternative to the positional path. `doctor` is optional local setup troubleshooting. Use `scan --help-all` for advanced collection options.

## Scope and graph meaning

| Scope | Behavior files included |
| --- | --- |
| `workspace` | Project files in the selected workspace and applicable repository ancestors; nested files retain their directory scope. |
| `global` | User, observable system, and managed behavior files. |
| `all` | Both groups. |

The project root is the nearest enclosing Git repository, or the selected directory when no repository is found. Ancestor discovery follows the path from that root to the selected workspace; it does not walk sibling projects. `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, and `COPILOT_HOME` select their documented user roots. Scope limits Source and Asset collection; agent configuration discovery is separate.

A `Source` records a file's presence and safe metadata. An `Influences` edge identifies the applicable agent, influence channel, activation, confidence, evidence, and scope. A file can affect several agents in different ways; inspect the edge properties for each relationship. A recognized file can remain an **unlinked** Source when it is overridden, unsupported by the selected agent, or outside its applicable hierarchy. A Source node alone does not establish loading.

Runtime configuration uses `influence_channel=runtime_configuration` and `activation=configured`. Ignore files use `context_visibility`. These relationships describe behavior configuration and are not prompt-injection entry points merely because an `Influences` edge exists.

| Activation | Interpretation |
| --- | --- |
| `automatic` | Documented loading for the applicable startup scope. |
| `conditional` | Depends on a target file, selected rule, hook event, plugin policy, or another unverified condition. |
| `on_demand` | Available for invocation or an optional read; presence does not prove use. |
| `configured` | A relevant configuration or explicit reference is present; effective runtime use remains unverified. |
| `observed` | Reserved for independently observed use; file presence never assigns it. |
| `session_resume` | Reserved for session-dependent loading; session-state discovery is not enabled. |
| `unknown` | Loading metadata could not be established safely. |

**Confirmed** means documented loading behavior and applicable scope are known. It does not prove that a running session consumed the file, that an attack succeeds, or that runtime trust and permissions allow an action. Nested/path-matched rules, reusable prompts, skills, hook scripts, plugin candidates, and unreadable or oversized metadata remain suspected when their use is unverified.

## Claude Code

Let `CLAUDE_HOME` mean `CLAUDE_CONFIG_DIR`, or `~/.claude` when unset.

| Category | Supported files |
| --- | --- |
| Project instructions | Applicable ancestor `CLAUDE.md`, `.claude/CLAUDE.md`, and `CLAUDE.local.md`; nested `CLAUDE.md` and `CLAUDE.local.md` are conditional. |
| Project rules | `.claude/rules/**/*.md`, including nested rule directories; a `paths` declaration makes applicability conditional. |
| Reusable definitions | `.claude/commands/**/*.md`, `.claude/agents/**/*.md`, `.claude/skills/**/SKILL.md`, and `.claude/output-styles/**/*.md`. Nested project skill trees are also recognized. |
| Personal definitions | `CLAUDE_HOME/CLAUDE.md`, `rules/**/*.md`, `commands/**/*.md`, `agents/**/*.md`, `skills/**/SKILL.md`, and `output-styles/**/*.md`. |
| Configuration | Project `.claude/settings.json`, `.claude/settings.local.json`, `.mcp.json`; `CLAUDE_HOME/settings.json`; `~/.claude.json`, or `CLAUDE_HOME/.claude.json` with a custom configuration directory. |
| Managed files | Locally present `CLAUDE.md`, `managed-settings.json`, `managed-settings.d/*.json`, and `managed-mcp.json` under the documented OS-specific managed root; documented managed skill/output-style directories. |
| Auto-memory | The selected repository's `CLAUDE_HOME/projects/<project-key>/memory/MEMORY.md` and sibling Markdown topic files, or an explicitly configured absolute/`~/` `autoMemoryDirectory`. |
| Explicit memory references | Literal `@path` references to Markdown files directly under `CLAUDE_HOME/projects/<project-key>/memory/`, including another repository's memory when the importing declaration and user scope are included. |
| Subagent memory | Markdown files in a subagent's explicitly declared `memory: user`, `project`, or `local` directory. |
| Hooks/plugins | Command-hook definitions, literal local script references, enabled skills-directory plugins, and selected installed plugin components with the cache option below. |

Managed roots are `/etc/claude-code` on Linux, `/Library/Application Support/ClaudeCode` on macOS, and `%ProgramFiles%/ClaudeCode` on Windows. Only files present on disk are inspected; remote policy, registry delivery, and effective enforcement are not inferred.

Instructions in the active ancestor chain are distinguished from nested instructions. Supported `instructionFiles` modes, the AGENTS compatibility plugin controls, and `claudeMdExcludes` affect applicable instruction candidates. `AGENTS.md` compatibility remains suspected because the installed Claude version and built-in plugin availability are unverified. Selected output styles are marked configured; unselected styles remain on demand.

Documented `@path` imports are followed with cycle detection, a depth limit of four, and a limit of 128 referenced files. Code fences, inline code, and HTML comments are not interpreted as imports. External imports stay within approved roots and retain unverified approval evidence.

Default auto-memory is resolved for the selected repository, including recognized Git worktree pointers. Agent-Hound never enumerates every project memory directory. `MEMORY.md` is an automatic startup candidate; topic files are on demand. Custom memory directories and subagent memory retain suspected runtime applicability. Explicit memory-disable settings are respected. Long project-key hashing is not guessed.

Another repository's memory is included when an in-scope instruction explicitly references its literal Markdown path, or configuration explicitly selects its memory directory. A literal import records only that file as a user-scoped `Memory` Source: it does not enumerate siblings, read its body, or follow further imports. Activation follows the importing declaration and confidence remains suspected. `workspace` excludes external user memory; `all` includes an applicable explicit reference. `global` can follow it when the importing declaration is itself in global scope. Unreferenced memories from other repositories remain outside automatic discovery.

Claude plugin discovery follows enabled declarations and supported `.claude-plugin/plugin.json` manifests. Skills-directory plugins load only from the primary working directory or personal skill root; ancestor/nested plugin components are not mistaken for ordinary skills. Declared/default skills, commands, agents, output styles, hook definitions/scripts, MCP files, and LSP files are recorded. MCP/LSP files remain runtime configuration. Plugin hook-disabling controls are respected; no plugin code runs.

Not inferred: out-of-repository ancestors, runtime `--add-dir` / `--settings` / `--plugin-dir`, synced account plugins, or MCP `.mcpb` / `.dxt` archive contents. Explicit project-directory names and custom memory directories are supported; undocumented long-key hashes are omitted.

References: [Memory and imports](https://code.claude.com/docs/en/memory), [settings](https://code.claude.com/docs/en/settings), [managed settings](https://code.claude.com/docs/en/managed-settings), [skills](https://code.claude.com/docs/en/skills), [subagents](https://code.claude.com/docs/en/sub-agents), [output styles](https://code.claude.com/docs/en/output-styles), [hooks](https://code.claude.com/docs/en/hooks), [plugin manifests](https://code.claude.com/docs/en/plugins/manifest-reference), [plugin loading](https://code.claude.com/docs/en/plugins/loading).

## Codex

Let `CODEX_HOME` mean the environment override, or `~/.codex`.

| Category | Supported files |
| --- | --- |
| Global instructions | `CODEX_HOME/AGENTS.override.md`, otherwise `CODEX_HOME/AGENTS.md`; the first nonempty file is selected. |
| Project instructions | `AGENTS.override.md`, `AGENTS.md`, then configured `project_doc_fallback_filenames`, in that order, with at most one nonempty file per directory. |
| Skills | `.agents/skills/**/SKILL.md` in the active ancestor chain, `~/.agents/skills/**/SKILL.md`, existing `CODEX_HOME/skills/**/SKILL.md`, and observable `/etc/codex/skills/**/SKILL.md`. Bundled `.system` skills retain system scope. |
| Configuration | Applicable `.codex/config.toml` layers, `CODEX_HOME/config.toml`, and locally present Unix `/etc/codex/config.toml`. |
| Explicit references | `agents.<name>.config_file`, `model_instructions_file`, the deprecated instructions-file alias, and `experimental_compact_prompt_file`. |
| Hooks | Applicable `.codex/hooks.json`, `CODEX_HOME/hooks.json`, Unix `/etc/codex/hooks.json`, inline hooks, and literal local script references. |
| Plugins | Repo/personal `.agents/plugins/marketplace.json` catalogs and explicitly enabled local `name@marketplace` declarations; selected cache candidates require the cache option. |

Startup instruction order follows repository root to working directory; deeper applicable guidance has later precedence. Descendants below the selected workspace are conditional candidates, and sibling subtrees are excluded. Overridden files do not receive instruction edges. `project_doc_max_bytes` bounds active project-chain availability; a partially available instruction prefix is distinguished from a fully available file. Configured fallback names cannot escape their directory.

Role-config references resolve relative to their declaring config, with cycle detection, a depth limit of four, and a 128-file limit. Explicit references remain within approved project, Codex home, user `.agents`, or system roots. Skill disable rules by path/name and explicit hook-disable settings are honored. Project trust, profile selection, CLI overrides, and runtime admission remain unverified.

Enabled local plugin declarations support portable root `plugin.json` with `skills/` and `mcp.json`, plus the `.codex-plugin/plugin.json` compatibility format. `extensions.com.openai` replaces the legacy overlay when present. Declared skills, onboarding skills, hook files/scripts, and MCP configuration are discovered. A marketplace source tree is a suspected candidate because the installed copy may differ.

Not inferred: selected `CODEX_HOME/<profile>.config.toml` profiles, cloud/account-managed enabled states, remote/npm marketplace downloads, Windows managed-root aliases, or linked-worktree main-checkout hook aliases. Undocumented Codex plugin agent/command fields are not guessed.

References: [AGENTS.md precedence](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [configuration layers](https://learn.chatgpt.com/docs/config-file/config-basic), [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference), [skills](https://learn.chatgpt.com/docs/build-skills), [hooks](https://learn.chatgpt.com/docs/hooks), [plugin packaging](https://developers.openai.com/plugins/build/plugins).

## GitHub Copilot and VS Code

Let `COPILOT_HOME` mean the environment override, or `~/.copilot`. The adapter targets VS Code Copilot and Copilot CLI; CLI-only configuration is not attributed to VS Code.

| Category | Supported files |
| --- | --- |
| Project instructions | `.github/copilot-instructions.md`, `.github/instructions/**/*.instructions.md`, applicable `AGENTS.md`, `CLAUDE.md`, `.claude/CLAUDE.md`, and `GEMINI.md`; nested applicability remains conditional. |
| Reusable definitions | VS Code `.github/prompts/**/*.prompt.md`; `.github/agents/**/*.agent.md`, retained legacy `.github/agents/**/*.md`, compatible `.claude/agents/**/*.md`; `.github/skills/**/SKILL.md`, `.claude/skills/**/SKILL.md`, `.agents/skills/**/SKILL.md`. |
| Personal definitions | `COPILOT_HOME/copilot-instructions.md`, `instructions/**/*.instructions.md`, `agents/**/*.agent.md`, `skills/**/SKILL.md`, plus `~/.agents/skills/**/SKILL.md`. |
| Configuration | `.github/copilot/settings.json`, `.github/copilot/settings.local.json`, supported `.claude/settings.json` and `.claude/settings.local.json`, `.github/mcp.json`, `.mcp.json`, and `COPILOT_HOME/settings.json` / `mcp-config.json`. VS Code also uses `.vscode/settings.json` and `.vscode/mcp.json`. |
| Hooks | `.github/hooks/**/*.json`, `COPILOT_HOME/hooks/**/*.json`, inline settings/agent-scoped hooks, and literal referenced scripts. |
| CLI runtime configuration | `.github/lsp.json`, `COPILOT_HOME/lsp-config.json`, `COPILOT_HOME/providers.json`; observable managed `policy.d/*.json` files. These are not prompt instructions. |
| Extra locations/plugins | Enabled literal VS Code instruction/agent/skill location maps; explicitly enabled local plugin declarations and registered local plugin roots. Installed packages require the cache option. |

Managed CLI policy directories are `/etc/github-copilot/policy.d` or `%ProgramData%/GitHub/Copilot/policy.d`. VS Code location maps are read only from supported settings already in scope; external escapes and disabled locations are skipped.

Instruction ancestry and safe `applyTo` metadata distinguish automatic from path-scoped loading. Relative instruction references use bounded traversal with cycle detection, a depth limit of five, and a 128-file limit. There is no assumed universal precedence among all Copilot instruction formats. Prompt presence does not prove that the current Copilot harness invoked it.

Plugin discovery supports documented legacy manifests and Agent Plugins schemas 1.0/1.1, including portable skills/MCP and `com.github.copilot` agents, commands, rules, hooks, and LSP components. Explicit disables override shared enabled declarations. Unknown standard schema versions do not contribute components. Hook disabling suppresses script expansion as applicable; execution is always unverified.

Not inferred: editor profile directories/databases, `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`, `COPILOT_SKILLS_DIRS`, custom `COPILOT_PROVIDERS_CONFIG`, unobserved additional CLI roots, or transient disabled-instruction state. General `config.json`, authentication stores, session state, and plugin data are not scanned.

References: [CLI instructions](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions), [configuration directory](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference), [hooks](https://docs.github.com/en/copilot/reference/hooks-reference), [plugin formats](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference), [VS Code instructions](https://code.visualstudio.com/docs/agents/reference/custom-instructions-reference), [VS Code plugins](https://code.visualstudio.com/docs/agents/reference/agent-plugin-reference), [VS Code settings](https://code.visualstudio.com/docs/agents/reference/ai-settings).

## Cursor

| Category | Supported files |
| --- | --- |
| Instructions/rules | Project `.cursorrules`, applicable ancestor/nested `AGENTS.md`, and nested `.cursor/rules/**/*.mdc`. Plain `.md` project rule files are not recognized. |
| Skills | Project/nested `.cursor/skills/**/SKILL.md`, `.agents/skills/**/SKILL.md`, and documented `.claude/skills` / `.codex/skills` compatibility; personal equivalents under `~`. |
| Subagents | Project and personal `.cursor/agents/**/*.md`, plus documented `.claude/agents` / `.codex/agents` compatibility. Project definitions take precedence over personal definitions; Cursor paths precede compatibility paths on filename conflicts. |
| Runtime/visibility | Project and personal `.cursor/mcp.json`, project `.cursorignore` and `.cursorindexingignore`. Ignore contents are not exported, and no terminal/MCP access restriction is inferred. |
| Hooks | Project and personal `.cursor/hooks.json` and literal local script references. |
| Declared local plugins | `~/.cursor/plugins/local/*/.cursor-plugin/plugin.json` or supported portable `plugin.json`; native rules, skills, agents, commands, hooks, and MCP configuration. |

Rule metadata distinguishes `alwaysApply`, file-pattern matching, description-based selection, and manual invocation. Root/active-chain always-on rules are automatic; nested applicability remains conditional. Rule `@file` mentions create suspected on-demand sources, not automatically inlined imports. User/team rules are not extracted from undocumented editor databases.

Native plugin component paths replace their default directories. Portable plugins provide skills and MCP configuration only. The documented `~/.cursor/plugins/local` import directory is not a cache; its declared manifests are available to normal global/all discovery without `--include-plugin-cache`. Import policy, marketplace overrides, and runtime activation remain unverified. Merely finding a plugin elsewhere does not enable its components.

Not inferred: undocumented user/team rule storage, marketplace cache paths, extension-registered roots, or paths returned by `workspaceOpen` hooks. Cursor CLI `cli-config.json` and undocumented legacy command directories are outside this adapter's coverage.

References: [Rules](https://cursor.com/docs/rules), [skills](https://cursor.com/docs/skills), [subagents](https://cursor.com/docs/subagents), [MCP](https://cursor.com/docs/mcp), [ignore files](https://cursor.com/docs/reference/ignore-file), [hooks](https://cursor.com/docs/hooks), [plugins](https://cursor.com/docs/plugins), [plugin formats](https://cursor.com/docs/reference/plugins).

## Existing sources and privacy limits

Existing `README.md`, scoped documentation, `GEMINI.md`, and `SYSTEM_PROMPT.md` discovery is retained. General documents remain suspected/on-demand sources. Platform-specific instructions target their supported agents; filename similarity alone never establishes an edge. Existing MCP resource/prompt advertisement discovery is preserved and remains distinct from reading their bodies.

Plugin caches are excluded by default. To inspect selected enabled or explicitly declared installed components:

```bash
agenthound scan /path/to/project --include-plugin-cache -o graph.json
```

This option does not enable disabled plugins, crawl every cached package, fetch remote marketplaces, or execute components. Cached versions and workspace/account activation can still be unverified. Local declared plugin roots can be inspected without cache traversal. Live MCP metadata discovery remains a separate explicit `--discover-mcp` operation.

Metadata reads are limited to 64 KiB per file. Only bounded metadata, frontmatter selectors, import references, and configuration references are parsed. Exports include paths, scope, activation, origin, precedence, safe target patterns, and Skill name/description where available, with `content_exported=false`. Instruction, prompt, memory, script, and session bodies; raw settings; environment/header values; credentials; and secret command arguments are not exported. Hook commands are omitted from Capability output, and malformed capability configuration errors omit parser input values.

Discovery does not execute configured hooks, scripts, plugin code, or command text found in files. Symlink escapes outside an approved root are rejected; directory walks do not follow linked trees. Existing Git metadata and capability collectors retain their established behavior, including the normal scan's connectivity probe when applicable. VCS internals, dependencies, virtual environments, caches, build outputs, vendored code, unreferenced memory from other repositories, and session/transcript stores are excluded from automatic discovery. No session-state collection is enabled by default. Verbose scan output reports counts by platform, source kind, scope, and activation without printing contents. Each Source is counted once per reported value; a shared file can appear under several platforms or loading conditions.

Coverage deliberately excludes unobserved CLI/profile overrides, remote organization or account policy, authentication/permissions stores, undocumented Cursor user-rule paths, arbitrary external reference roots, shell-generated paths, YAML tags/aliases and multiline hook bodies, and unsupported plugin schemas/components. Selected or explicitly referenced/configured Claude memory and declared plugin roots are narrow exceptions to broad state-directory exclusions. Source discovery does not expand the capability model beyond existing collectors; capability parsing is hardened for bounded reads and secret-safe output. A missing edge can indicate unsupported or unobserved loading, rather than proven isolation.
