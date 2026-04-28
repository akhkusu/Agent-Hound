"""Agent-Hound CLI."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from agenthound import __version__
from agenthound.collectors.assets import collect_assets
from agenthound.collectors.base import CollectionResult
from agenthound.collectors.capabilities import collect_from_config, collect_from_configs
from agenthound.collectors.impact import check_internet, collect_impact
from agenthound.collectors.sources import collect_sources
from agenthound.discovery.config_parser import discover_config_files
from agenthound.output.opengraph import build_opengraph, write_opengraph
from agenthound.platforms import CUSTOM_TYPES

console = Console()


@click.group(invoke_without_command=True)
@click.option("--config", "-c", type=click.Path(exists=True, path_type=Path), help="Agent config file.")
@click.option("--workspace", "-w", type=click.Path(exists=True, path_type=Path),
              default=".", show_default=True, help="Workspace directory to scan for sources.")
@click.option("--scope", type=click.Choice(["workspace", "global", "all"]), default="all",
              show_default=True, help="Scanning scope for assets.")
@click.option("--output", "-o", type=click.Path(path_type=Path), default="agenthound_output.json",
              show_default=True, help="Output file path.")
@click.option("--verbose", "-v", is_flag=True)
@click.version_option(version=__version__)
@click.pass_context
def cli(ctx: click.Context, config: Path | None, workspace: Path, scope: str,
        output: Path, verbose: bool) -> None:
    """Agent-Hound — BloodHound OpenGraph collector for AI agent attack paths."""
    ctx.ensure_object(dict)
    ctx.obj.update({"workspace": workspace, "scope": scope, "output": output, "verbose": verbose})
    if ctx.invoked_subcommand is None:
        if config is None:
            click.echo(ctx.get_help())
            return
        _run_scan([config], workspace, scope, output, verbose)


@cli.command()
@click.option("--workspace", "-w", type=click.Path(exists=True, path_type=Path),
              default=".", show_default=True)
@click.option("--scope", type=click.Choice(["workspace", "global", "all"]), default="all",
              show_default=True)
@click.option("--output", "-o", type=click.Path(path_type=Path), default="agenthound_output.json",
              show_default=True)
@click.option("--verbose", "-v", is_flag=True)
def discover(workspace: Path, scope: str, output: Path, verbose: bool) -> None:
    """Auto-discover agent configs and scan for attack paths."""
    config_paths = discover_config_files()
    if not config_paths:
        console.print("[yellow]No agent config files found.[/yellow]")
        return
    if verbose:
        console.print(f"[blue]Found {len(config_paths)} config file(s)[/blue]")
        for p in config_paths:
            console.print(f"  {p}")
    _run_scan(config_paths, workspace, scope, output, verbose)


def _run_scan(config_paths: list[Path], workspace: Path, scope: str, output: Path, verbose: bool) -> None:
    console.print(f"[bold blue]Agent-Hound v{__version__}[/bold blue]")
    console.print()

    cap_result = collect_from_configs(config_paths)
    internet = check_internet()

    if verbose:
        _print_scope_summary(config_paths, workspace, scope, internet)

    # Collect all pillars
    combined = CollectionResult()
    combined.agents = cap_result.agents
    combined.capabilities = cap_result.capabilities
    combined.edges.extend(cap_result.edges)

    for agent in cap_result.agents:
        src = collect_sources(agent=agent, workspace=workspace, scope=scope)
        combined.sources.extend(src.sources)
        combined.edges.extend(src.edges)

    asset_result = collect_assets(capabilities=cap_result.capabilities, workspace=workspace, scope=scope)
    combined.assets = asset_result.assets
    combined.edges.extend(asset_result.edges)

    source_repos = [a.path for a in combined.assets if a.asset_kind == "SourceCode"]
    impact_result = collect_impact(
        capabilities=cap_result.capabilities,
        internet_reachable=internet,
        source_repos=source_repos,
    )
    combined.impacts = impact_result.impacts
    combined.edges.extend(impact_result.edges)

    og = build_opengraph(combined)
    write_opengraph(og, str(output))

    console.print(f"[green]Output written to {output}[/green]")
    console.print(f"  Nodes: {combined.total_nodes} | Edges: {combined.total_edges}")
    _print_findings(combined, internet)


def _print_scope_summary(config_paths: list[Path], workspace: Path, scope: str, internet: bool) -> None:
    table = Table(title="Scope Summary")
    table.add_column("Pillar", style="cyan")
    table.add_column("Scanning", style="green")
    table.add_row("Capabilities", ", ".join(str(p.name) for p in config_paths))
    table.add_row("Sources", str(workspace) if scope in ("workspace", "all") else "skipped")
    table.add_row("Assets", f"home dir + {workspace}" if scope == "all" else scope)
    table.add_row("Impact", f"connectivity={'reachable' if internet else 'blocked'} + shell + git")
    console.print(table)
    console.print()


def _print_findings(result: CollectionResult, internet: bool) -> None:
    findings: list[str] = []
    ipi_sources = result.sources
    if ipi_sources:
        findings.append(
            f"[yellow]{len(ipi_sources)} IPI source(s): "
            f"{', '.join(s.name for s in ipi_sources[:3])}"
            f"{'...' if len(ipi_sources) > 3 else ''}[/yellow]"
        )
    privileged_caps = [c for c in result.capabilities if c.has_shell]
    if privileged_caps and result.assets:
        findings.append(
            f"[red]{len(result.assets)} asset(s) reachable via shell capability[/red]"
        )
    if internet and any(i.impact_kind == "Exfiltration" for i in result.impacts):
        findings.append("[red]Exfiltration path detected: internet is reachable[/red]")
    if findings:
        console.print()
        console.print("[bold]Security Findings:[/bold]")
        for f in findings:
            console.print(f"  {f}")


@cli.command("register-icons")
@click.option("--bh-url", default="http://localhost:8080", show_default=True)
@click.option("--username", "-u", default="admin", show_default=True)
@click.option("--password", "-p", prompt=True, hide_input=True)
def register_icons(bh_url: str, username: str, password: str) -> None:
    """Upload Agent-Hound node icons to a running BloodHound CE instance."""
    import json as _json
    import urllib.error
    import urllib.request

    base = bh_url.rstrip("/")
    login_payload = _json.dumps({
        "login_method": "secret", "username": username, "secret": password,
    }).encode()
    req = urllib.request.Request(f"{base}/api/v2/login", data=login_payload, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            token = _json.loads(resp.read())["data"]["session_token"]
    except urllib.error.HTTPError as e:
        console.print(f"[red]Login failed: HTTP {e.code}[/red]")
        return
    except urllib.error.URLError as e:
        console.print(f"[red]Connection failed: {e.reason}[/red]")
        return
    except (KeyError, ValueError):
        console.print("[red]Login failed: unexpected response format[/red]")
        return

    model_payload = _json.dumps({"custom_types": CUSTOM_TYPES}).encode()
    model_req = urllib.request.Request(f"{base}/api/v2/custom-nodes", data=model_payload, method="POST")
    model_req.add_header("Authorization", f"Bearer {token}")
    model_req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(model_req) as resp:
            console.print(f"[green]Icons registered (HTTP {resp.status})[/green]")
            for kind, cfg in CUSTOM_TYPES.items():
                console.print(f"  [cyan]{kind}[/cyan]: {cfg['icon']['name']}")
    except urllib.error.HTTPError as e:
        console.print(f"[red]HTTP {e.code}: {e.reason}[/red]")
    except urllib.error.URLError as e:
        console.print(f"[red]Connection failed: {e.reason}[/red]")


if __name__ == "__main__":
    cli()
