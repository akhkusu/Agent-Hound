# Using the CLI

The two main commands are `scan` and `doctor`. Both accept an optional project path; omitting it uses the current directory.

```bash
agenthound doctor ./my-project
agenthound scan ./my-project -o graph.json
```

`scan` exports a BloodHound OpenGraph file and previews attack paths in the terminal. Without `-o`, it writes `agenthound_output.json`. Import the JSON through **File Ingest** in BloodHound CE.

## Check the setup

`doctor` checks local configuration and reports `OK`, `WARNING`, or `ERROR` with suggested next steps. It does not create a graph, launch MCP servers, authenticate to cloud services, or perform network checks. It cannot verify live server availability or cloud permissions. Errors return exit code 1; warnings alone return 0.

To check or scan one configuration without merging user, project, and local settings:

```bash
agenthound doctor --config /path/to/config.json
agenthound scan ./my-project --config /path/to/config.json -o graph.json
```

## Choose what to collect

Automatic scans discover supported user and project configurations. The default `--scope all` includes workspace and home assets plus project and user/system skill sources. To restrict source and asset collection to the project:

```bash
agenthound scan ./my-project --scope workspace
```

`--scope` controls sources and assets; it does not restrict configuration discovery to the project. Use `--config` when you want exactly one configuration file.

Normal scans may check internet connectivity when capabilities are found. MCP servers are read from configuration without being started or contacted. Live MCP collection requires `--discover-mcp`; live Microsoft 365 collection requires `--m365`.

## Find advanced features

```bash
agenthound --help
agenthound scan --help
agenthound --help-all
agenthound scan --help-all
```

The standard help focuses on common operations. Root `--help-all` also lists the specialist commands for Copilot inventory, SharePoint metadata, M365 snapshot imports, AD/GitHub graph bridges, and BloodHound icons. `scan --help-all` shows all collection options. Specialist commands remain directly callable; use `<command> --help` for their options.

See [Microsoft 365 integration](m365-integration.md), [AD integration](ad-integration.md), and [GitHound integration](github-integration.md) for complete workflows.

## Update existing commands

The former `discover` command has been replaced by `scan`. Move any root-level collection options after `scan`: the former `agenthound --config FILE` form is now `agenthound scan --config FILE`. The `--discover-mcp` flag keeps its existing name.

The `-w`/`--workspace` option is still available on `scan` as an alternative to the positional project path. Supply one or the other, rather than both. Root options are limited to version and help.
