"""Agent-Hound CLI."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from agenthound import __version__
from agenthound.collectors.assets import collect_assets
from agenthound.collectors.base import CollectionResult
from agenthound.collectors.capabilities import collect_from_configs
from agenthound.collectors.impact import check_internet, collect_impact
from agenthound.collectors.sources import collect_sources
from agenthound.discovery.config_parser import discover_config_files
from agenthound.discovery.runtime_identity import (
    RuntimeIdentityError,
    observe_claude_pid,
)
from agenthound.models.edges import Edge
from agenthound.output.ad_bridge import build_ad_bridge
from agenthound.output.github_bridge import build_github_bridge
from agenthound.output.opengraph import build_opengraph, write_opengraph
from agenthound.platforms import CUSTOM_TYPES

console = Console()


@click.group(invoke_without_command=True)
@click.option("--config", "-c", type=click.Path(exists=True, path_type=Path), help="Scan only this config file; do not merge user/project/local settings.")
@click.option("--workspace", "-w", type=click.Path(exists=True, path_type=Path),
              default=".", show_default=True, help="Workspace directory to scan for sources.")
@click.option("--scope", type=click.Choice(["workspace", "global", "all"]), default="all",
              show_default=True, help="Scanning scope for assets.")
@click.option("--output", "-o", type=click.Path(path_type=Path), default="agenthound_output.json",
              show_default=True, help="Output file path.")
@click.option("--verbose", "-v", is_flag=True)
@click.option("--claude-pid", type=click.IntRange(min=1), help="Observe this local Windows claude.exe process owner SID.")
@click.option("--process-identity", is_flag=True, help="Collect PID and owner SID of the sole local Windows claude.exe process.")
@click.version_option(version=__version__)
@click.pass_context
def cli(ctx: click.Context, config: Path | None, workspace: Path, scope: str,
        output: Path, verbose: bool, claude_pid: int | None, process_identity: bool) -> None:
    """Agent-Hound — BloodHound OpenGraph collector for AI agent attack paths."""
    ctx.ensure_object(dict)
    ctx.obj.update({"workspace": workspace, "scope": scope, "output": output, "verbose": verbose})
    if ctx.invoked_subcommand is None:
        if config is None:
            click.echo(ctx.get_help())
            return
        _run_scan([config], workspace, scope, output, verbose, claude_pid=claude_pid,
                  process_identity=process_identity)


@cli.command()
@click.option("--workspace", "-w", type=click.Path(exists=True, path_type=Path),
              default=".", show_default=True)
@click.option("--scope", type=click.Choice(["workspace", "global", "all"]), default="all",
              show_default=True)
@click.option("--output", "-o", type=click.Path(path_type=Path), default="agenthound_output.json",
              show_default=True)
@click.option("--verbose", "-v", is_flag=True)
@click.option("--claude-pid", type=click.IntRange(min=1), help="Observe this local Windows claude.exe process owner SID.")
@click.option("--process-identity", is_flag=True, help="Collect PID and owner SID of the sole local Windows claude.exe process.")
@click.option("--copilot/--no-copilot", default=True, show_default=True,
              help="Include local Windows Copilot observations when LocalAppData is available.")
@click.option("--local-app-data", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Copilot LocalAppData root, including an authorized offline copy.")
@click.option("--include-registry", is_flag=True, help="Include current-user Copilot cached registry flags.")
@click.option("--m365", is_flag=True, help="Also collect live SharePoint metadata using the existing Copilot WAM account.")
@click.option("--m365-snapshot", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Merge observed M365 permissions and document references.")
@click.option("--site-url", help="Limit live M365 discovery to this SharePoint site.")
@click.option("--copilot-client-id", help="Existing configured WAM client ID, if selection is ambiguous.")
@click.option("--limit", type=click.IntRange(1, 100000), default=20, show_default=True,
              help="Maximum live SharePoint file metadata records retained.")
@click.option("--enumerate-all", is_flag=True,
              help="Finish recursive SharePoint enumeration; --limit still bounds retained document nodes.")
def discover(workspace: Path, scope: str, output: Path, verbose: bool, claude_pid: int | None,
             process_identity: bool, copilot: bool, local_app_data: Path | None,
             include_registry: bool, m365: bool, m365_snapshot: Path | None,
             site_url: str | None, copilot_client_id: str | None, limit: int,
             enumerate_all: bool) -> None:
    """Discover local agents and export one graph with an Attack Paths preview."""
    import os

    from pydantic import ValidationError

    from agenthound.discovery.copilot_windows import (
        copilot_inventory_graph,
        inventory_copilot_windows,
    )
    from agenthound.discovery.m365_files import M365FilesError
    from agenthound.discovery.m365_snapshot import M365Snapshot
    from agenthound.output.m365 import build_m365_graph
    from agenthound.output.unified import build_metadata_graph

    if not copilot and (local_app_data is not None or include_registry):
        raise click.ClickException("--local-app-data and --include-registry require --copilot")
    if not m365 and (site_url is not None or copilot_client_id is not None or limit != 20 or enumerate_all):
        raise click.ClickException("--site-url, --copilot-client-id, --limit and --enumerate-all require --m365")
    if m365_snapshot is not None and m365_snapshot.resolve() == output.resolve():
        raise click.ClickException("Snapshot and graph output paths must differ")
    extra_graphs = []
    try:
        if copilot and (local_app_data is not None or os.environ.get("LOCALAPPDATA")):
            inventory = inventory_copilot_windows(local_app_data, include_registry=include_registry)
            _validate_inventory_destinations(inventory, [output])
            extra_graphs.append(copilot_inventory_graph(inventory))
        elif include_registry:
            raise ValueError("Copilot inventory requires --local-app-data or LOCALAPPDATA")
        if m365_snapshot is not None:
            snapshot = M365Snapshot.model_validate_json(m365_snapshot.read_text(encoding="utf-8-sig"))
            extra_graphs.append(build_m365_graph(snapshot))
        if m365:
            from agenthound.discovery.copilot_files import collect_copilot_files

            listing = collect_copilot_files(site_url=site_url, client_id=copilot_client_id,
                                            display_limit=limit, enumerate_all=enumerate_all)
            extra_graphs.append(build_metadata_graph(listing))
            click.echo(f"SharePoint: {len(listing['items'])} file metadata records retained; content access unverified.")
            click.echo(f"SharePoint enumeration complete: {listing.get('enumeration_complete', False)}; tenant-wide coverage unverified.")
            if listing.get("errors"):
                click.echo(f"SharePoint collection errors: {len(listing['errors'])}; graph coverage is partial.")
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False))
        raise click.ClickException(f"Invalid M365 observation: {details}") from exc
    except (M365FilesError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    config_paths = discover_config_files(workspace)
    if verbose:
        console.print(f"[blue]Found {len(config_paths)} config file(s)[/blue]")
        for p in config_paths:
            console.print(f"  {p}")
    _run_scan(config_paths, workspace, scope, output, verbose, merge_claude=True,
              claude_pid=claude_pid, process_identity=process_identity, extra_graphs=extra_graphs)


def _validate_inventory_destinations(inventory: dict[str, Any], destinations: list[Path]) -> None:
    roots = [item["data_root"] for item in inventory["observations"]]
    roots.extend(store["path"] for store in (
        *inventory["shared_authentication_store_presence"].values(),
        *inventory["mcp_storage_presence"],
    ))
    for root in roots:
        if any(destination.resolve().is_relative_to(Path(root).resolve()) for destination in destinations):
            raise ValueError("Output must be outside application, authentication and MCP data roots")


@cli.command("copilot-windows")
@click.option("--include-registry", is_flag=True,
              help="Also inspect known live HKCU Copilot keys (not the offline copy).")
@click.option("--local-app-data", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Windows LocalAppData root, or an authorized offline copy.")
@click.option("--output", "-o", type=click.Path(path_type=Path),
              default="copilot_windows_inventory.json", show_default=True)
@click.option("--graph-output", type=click.Path(path_type=Path), help="Optional OpenGraph inventory output.")
def copilot_windows(local_app_data: Path | None, output: Path, graph_output: Path | None,
                    include_registry: bool) -> None:
    """Inventory Copilot/OfficeHub locations and known cached policy booleans."""
    import json

    from agenthound.discovery.copilot_windows import (
        copilot_inventory_graph,
        inventory_copilot_windows,
    )

    try:
        if graph_output is not None and graph_output.resolve() == output.resolve():
            raise ValueError("Inventory and graph output paths must differ")
        inventory = inventory_copilot_windows(local_app_data, include_registry=include_registry)
        _validate_inventory_destinations(inventory, [p for p in (output, graph_output) if p is not None])
        output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if graph_output is not None:
            write_opengraph(copilot_inventory_graph(inventory), str(graph_output))
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Inventory written to {output} ({len(inventory['observations'])} data root(s))")
    click.echo("File access assessment:")
    click.echo("  Local file search/read: unverified (no runtime retrieval tested)")
    for item in inventory["observations"]:
        if item["product"] != "copilot-windows":
            continue
        locations = [("Local State", item.get("local_file_permission_settings", {}))]
        locations.extend(
            (p["profile"] + "/Preferences", p["local_file_permission_settings"])
            for p in item.get("profiles", [])
        )
        for location, flags in locations:
            click.echo(f"    {location}: search={flags.get('is_file_search_allowed', 'unknown')}, "
                       f"read={flags.get('is_file_read_allowed', 'unknown')}")
    click.echo("  SharePoint file metadata: use m365-files; observed ACLs: use m365-import with a snapshot")
    click.echo("Cached flags and authentication-store presence do not prove file access.")


@cli.command("ad-bridge")
@click.option("--agent-graph", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--ad-zip", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True)
def ad_bridge(agent_graph: Path, ad_zip: Path, output: Path) -> None:
    """Create AD edges only for observed SIDs found in SharpHound data."""
    import json

    try:
        payload = build_ad_bridge(json.loads(agent_graph.read_text(encoding="utf-8")), ad_zip)
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as exc:
        raise click.ClickException(str(exc)) from exc
    write_opengraph(payload, str(output))
    click.echo(f"Output written to {output} ({len(payload['graph']['edges'])} verified AD link(s))")


@cli.command("github-bridge")
@click.option("--agent-graph", type=click.Path(exists=True, path_type=Path), required=True)
@click.option(
    "--githound-graph",
    type=click.Path(exists=True, path_type=Path),
    multiple=True,
    required=True,
)
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True)
def github_bridge(agent_graph: Path, githound_graph: tuple[Path, ...], output: Path) -> None:
    """Connect local repositories and clean sources to existing GitHound nodes."""
    import json

    try:
        agent_payload = json.loads(agent_graph.read_text(encoding="utf-8"))
        github_payloads = [
            json.loads(path.read_text(encoding="utf-8")) for path in githound_graph
        ]
        payload = build_github_bridge(agent_payload, github_payloads)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    write_opengraph(payload, str(output))
    edges = payload["graph"]["edges"]
    clone_count = sum(edge["kind"] == "AH_CloneOf" for edge in edges)
    source_count = sum(edge["kind"] == "AH_SuppliesSource" for edge in edges)
    click.echo(
        f"Output written to {output} "
        f"({clone_count} repository link(s), {source_count} supplied source(s))"
    )


@cli.command("m365-import")
@click.option("--snapshot", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True)
def m365_import(snapshot: Path, output: Path) -> None:
    """Import observed SharePoint ACLs and explicit Copilot references (no network)."""
    from pydantic import ValidationError

    from agenthound.discovery.m365_snapshot import M365Snapshot
    from agenthound.output.m365 import build_m365_graph

    try:
        if snapshot.resolve() == output.resolve():
            raise ValueError("Snapshot and graph output paths must differ")
        data = M365Snapshot.model_validate_json(snapshot.read_text(encoding="utf-8-sig"))
        graph = build_m365_graph(data)
        write_opengraph(graph, str(output))
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False))
        raise click.ClickException(f"Invalid M365 snapshot: {details}") from exc
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Output written to {output} ({len(graph['graph']['nodes'])} nodes, {len(graph['graph']['edges'])} edges)")
    click.echo("ACL reachability is separate from Copilot retrieval; inspect confidence and evidence.")


@cli.command("m365-files")
@click.option("--api", type=click.Choice(["auto", "graph", "sharepoint"]),
              help="Defaults to recursive Copilot discovery without target arguments; explicit tokens/drive IDs use Graph.")
@click.option("--tenant-id", help="Target Entra tenant GUID; defaults to the current Copilot account.")
@click.option("--site-url", help="SharePoint site URL; uses its default document library.")
@click.option("--drive-id", help="Known Graph drive ID; skips site resolution.")
@click.option("--folder", default="", help="Folder relative to the library root, e.g. Project_ORION.")
@click.option("--library", default="Shared Documents", show_default=True,
              help="Library URL path for --api sharepoint; pass its decoded path.")
@click.option("--token-env", help="Environment variable containing an existing Graph token; never pass its value here.")
@click.option("--copilot-wam", is_flag=True, help="Silent Windows broker auth using the configured Copilot account.")
@click.option("--copilot-client-id", help="Select a client GUID already present in the current Copilot account's WAM mapping; needed if ambiguous.")
@click.option("--max-items", type=click.IntRange(1, 100000), default=10000, show_default=True)
@click.option("--limit", type=click.IntRange(1, 100000), default=20, show_default=True,
              help="Maximum displayed/saved item records; totals are reported separately.")
@click.option("--enumerate-all", is_flag=True,
              help="Finish recursive enumeration to count discovered locations; display remains limited.")
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Optional metadata JSON file.")
@click.option("--json", "json_stdout", is_flag=True, help="Write metadata JSON to stdout instead of a table.")
def m365_files(api: str | None, tenant_id: str | None, site_url: str | None, drive_id: str | None,
               folder: str, library: str, token_env: str | None, copilot_wam: bool,
               copilot_client_id: str | None, max_items: int, limit: int, enumerate_all: bool,
               output: Path | None, json_stdout: bool) -> None:
    """List live folder items through Microsoft Graph or SharePoint REST GETs."""
    import json

    from rich.text import Text

    from agenthound.discovery.m365_files import M365FilesError, collect_m365_files

    try:
        selected_api = api or ("graph" if token_env is not None or drive_id else
                               "sharepoint" if site_url and folder else "auto")
        if selected_api == "auto":
            from agenthound.discovery.copilot_files import collect_copilot_files

            if token_env is not None or drive_id or folder:
                raise M365FilesError("Auto discovery uses Copilot WAM; use --api sharepoint or graph for an explicit folder/token")
            listing = collect_copilot_files(tenant_id=tenant_id, site_url=site_url,
                                            client_id=copilot_client_id, display_limit=limit,
                                            max_items=max_items, enumerate_all=enumerate_all)
        else:
            if tenant_id is None:
                from agenthound.discovery.copilot_wam import discover_copilot_context

                tenant_id = discover_copilot_context()["tenant_id"]
        if selected_api == "sharepoint":
            from agenthound.discovery.sharepoint_files import collect_sharepoint_files

            assert tenant_id is not None  # Resolved above for explicit backends.
            if not site_url or drive_id:
                raise M365FilesError("--api sharepoint requires --site-url and does not accept --drive-id")
            listing = collect_sharepoint_files(
                tenant_id=tenant_id, site_url=site_url, folder=folder, library=library,
                token_env=token_env, copilot_wam=copilot_wam or token_env is None,
                copilot_client_id=copilot_client_id, max_items=max_items,
            )
        elif selected_api == "graph":
            assert tenant_id is not None  # Resolved above for explicit backends.
            if library != "Shared Documents":
                raise M365FilesError("--library is supported only with --api sharepoint")
            listing = collect_m365_files(
                tenant_id=tenant_id, site_url=site_url, drive_id=drive_id,
                folder=folder, token_env=token_env, copilot_wam=copilot_wam,
                copilot_client_id=copilot_client_id, max_items=max_items,
            )
        if selected_api != "auto":
            listed_items = listing["items"]
            listing["enumerated_item_count"] = len(listed_items)
            listing["enumerated_file_count"] = sum(item["kind"] == "file" for item in listed_items)
            listing["content_access_verified_count"] = None
            listing["display_limit"] = limit
            listing["items"] = listed_items[:limit]
            listing["displayed_item_count"] = len(listing["items"])
            listing["items_truncated"] = len(listed_items) > limit
        if output is not None:
            output.write_text(json.dumps(listing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (M365FilesError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    if json_stdout:
        click.echo(json.dumps(listing, ensure_ascii=False, indent=2))
    else:
        table = Table(title=f"SharePoint items (shown {len(listing['items'])}; enumerated file metadata {listing['enumerated_file_count']})")
        for title in ("Name", "Kind", "Bytes", "Modified", "URL"):
            table.add_column(title, overflow="fold")
        for item in listing["items"]:
            table.add_row(*(Text(str(item[key])) for key in
                            ("name", "kind", "size", "last_modified", "web_url")))
        console.print(table, markup=False)
        if listing.get("mode") == "recursive_discovery":
            total = listing["total_files_in_discovered_libraries"]
            click.echo(f"Total files in discovered libraries: {total if total is not None else 'unknown (partial enumeration)'}. Tenant-wide coverage: unverified.")
            if listing["errors"]:
                click.echo(f"Collection errors: {len(listing['errors'])}; use --json or --output for details.")
        click.echo("Document-content access count: unverified. Library-reported item counts can include folders.")
        click.echo("Live API listing; document contents, complete ACLs and Copilot retrieval were not collected.")
    if output is not None:
        click.echo(f"Metadata written to {output}", err=json_stdout)


def _run_scan(config_paths: list[Path], workspace: Path, scope: str, output: Path, verbose: bool,
              merge_claude: bool = False, claude_pid: int | None = None,
              process_identity: bool = False, extra_graphs: list[dict[str, Any]] | None = None) -> None:
    from agenthound.output.attack_paths import print_attack_paths
    from agenthound.output.unified import merge_graphs

    if process_identity and claude_pid is not None:
        raise click.ClickException("Use either --process-identity or --claude-pid, not both")
    console.print(f"[bold blue]Agent-Hound v{__version__}[/bold blue]")
    console.print()

    cap_result = collect_from_configs(config_paths, workspace=workspace if merge_claude else None)
    if process_identity or claude_pid is not None:
        claude_agents = [agent for agent in cap_result.agents if agent.name == "claude-code"]
        if len(claude_agents) != 1:
            raise click.ClickException("Runtime observation requires exactly one Claude Code agent in the scan")
        try:
            identity = observe_claude_pid(claude_pid)
        except RuntimeIdentityError as exc:
            raise click.ClickException(str(exc)) from exc
        cap_result.agents = [
            agent.model_copy(update={"runtime_pid": identity.pid, "runtime_owner_sid": identity.owner_sid,
                                     "runtime_observed_at": identity.observed_at,
                                     "runtime_computer_sid": identity.computer_sid})
            if agent == claude_agents[0] else agent for agent in cap_result.agents
        ]
    internet = check_internet() if cap_result.capabilities else False

    if verbose:
        _print_scope_summary(config_paths, workspace, scope, internet)

    # Collect all pillars
    combined = CollectionResult()
    combined.agents = cap_result.agents
    combined.capabilities = cap_result.capabilities
    combined.edges.extend(cap_result.edges)

    seen_source_ids: set[str] = set()
    for agent in cap_result.agents:
        src = collect_sources(agent=agent, workspace=workspace, scope=scope)
        # A source file is one node; only the Influences edge is per-agent.
        for source in src.sources:
            if source.objectid not in seen_source_ids:
                combined.sources.append(source)
                seen_source_ids.add(source.objectid)
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
    if extra_graphs:
        og = merge_graphs(og, *extra_graphs)
    write_opengraph(og, str(output))

    console.print(f"[green]Output written to {output}[/green]")
    console.print(f"  Nodes: {len(og['graph']['nodes'])} | Edges: {len(og['graph']['edges'])}")
    _print_findings(combined)
    print_attack_paths(og, console)


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


def _edge_confidence_note(edges: list[Edge]) -> str:
    confirmed = sum(1 for e in edges if e.properties.get("confidence") == "confirmed")
    return f"{confirmed} confirmed, {len(edges) - confirmed} suspected"


def _asset_confidence_note(edges: list[Edge]) -> tuple[int, str]:
    """Aggregate CanAccess edges per target asset, taking each asset's highest
    confidence, so the count reflects assets rather than edges."""
    best: dict[str, str] = {}
    for e in edges:
        conf = e.properties.get("confidence", "suspected")
        if best.get(e.end) != "confirmed":
            best[e.end] = conf
    confirmed = sum(1 for c in best.values() if c == "confirmed")
    return len(best), f"{confirmed} confirmed, {len(best) - confirmed} suspected"


def _print_findings(result: CollectionResult) -> None:
    """Summarize findings from the actual generated edges, showing how much of
    each is a verified fact versus a name-based guess (confidence)."""
    findings: list[str] = []
    # Only sources that actually influence an agent count as IPI entry points.
    influenced = {e.start for e in result.edges if e.kind == "Influences"}
    ipi_sources = [s for s in result.sources if s.objectid in influenced]
    if ipi_sources:
        findings.append(
            f"[yellow]{len(ipi_sources)} IPI source(s): "
            f"{', '.join(s.name for s in ipi_sources[:3])}"
            f"{'...' if len(ipi_sources) > 3 else ''}[/yellow]"
        )

    can_access = [e for e in result.edges if e.kind == "CanAccess"]
    if can_access:
        reachable_assets, note = _asset_confidence_note(can_access)
        findings.append(
            f"[red]{reachable_assets} sensitive asset(s) reachable via "
            f"capabilities ({note})[/red]"
        )

    impact_labels = [
        ("SystemTakeover", "System-takeover"),
        ("Exfiltration", "Internet-exfiltration"),
        ("SupplyChainContamination", "Supply-chain-contamination"),
    ]
    for kind, label in impact_labels:
        impact_ids = {i.objectid for i in result.impacts if i.impact_kind == kind}
        if not impact_ids:
            continue
        triggers = [
            e for e in result.edges if e.kind == "Triggers" and e.end in impact_ids
        ]
        findings.append(
            f"[red]{label} path detected ({_edge_confidence_note(triggers)})[/red]"
        )

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
