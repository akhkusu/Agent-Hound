# Changelog

All notable changes to Agent-Hound are documented here.

## [Unreleased]

### Added
- `doctor [PATH]` checks local configuration, platform support, and optional package metadata, with suggested fixes. It runs offline and does not create a graph.
- `--help-all` lists specialist commands; `scan --help-all` shows all collection options.

### Changed
- Replaced `discover` with `scan [PATH]`. Update existing scripts to use `scan`; omitting PATH uses the current directory.
- Moved collection options under `scan`: for example, `agenthound --config FILE` becomes `agenthound scan --config FILE`. Misplaced root options now fail instead of being silently ignored.
- Standard help focuses on `scan` and `doctor`. Existing specialist commands remain directly callable.

## [0.1.0] - 2026-04-28

### Added
- Four-pillar node model: Agent, Source, Capability, Asset, Impact
- Config parser for Claude Desktop, Claude Code, VS Code Copilot, Cursor, Windsurf
- Capability collector with HasCapability edges
- Source collector scanning workspace for IPI-injectable files
- Asset collector scanning filesystem for SSH keys, env files, cloud credentials
- Impact collector detecting Exfiltration, SupplyChain, and SystemTakeover paths
- BloodHound OpenGraph v6 JSON output
- CLI: `scan`, `discover`, `register-icons` commands
- `--scope` flag (workspace | global | all)
