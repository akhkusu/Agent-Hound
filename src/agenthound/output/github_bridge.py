"""Match local Git repositories and sources to GitHound nodes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agenthound.discovery.git_metadata import normalize_remote_url


def _nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    graph = payload.get("graph")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        raise ValueError("OpenGraph payload must contain graph.nodes")
    return graph["nodes"]


def _has_kind(node: dict[str, Any], *kinds: str) -> bool:
    node_kinds = node.get("kinds")
    return isinstance(node_kinds, list) and any(kind in node_kinds for kind in kinds)


def _properties(node: dict[str, Any]) -> dict[str, Any]:
    properties = node.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("OpenGraph node properties must be an object")
    return properties


def _node_id(node: dict[str, Any]) -> str:
    identifier = node.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("OpenGraph node id must be a non-empty string")
    return identifier


def _endpoint(identifier: str, kind: str | None = None) -> dict[str, str]:
    endpoint = {"value": identifier, "match_by": "id"}
    if kind:
        endpoint["kind"] = kind
    return endpoint


def _unique_index(
    entries: Iterable[tuple[object, dict[str, Any]]], label: str
) -> dict[object, dict[str, Any]]:
    result: dict[object, dict[str, Any]] = {}
    for key, node in entries:
        if key in result and _node_id(result[key]) != _node_id(node):
            raise ValueError(f"Ambiguous {label} match for {key}")
        result[key] = node
    return result


def _repository_key(node: dict[str, Any]) -> str | None:
    properties = _properties(node)
    html_url = properties.get("html_url")
    if isinstance(html_url, str):
        normalized = normalize_remote_url(html_url)
        if normalized:
            return normalized
    full_name = properties.get("full_name")
    if isinstance(full_name, str):
        return normalize_remote_url(f"https://github.com/{full_name}")
    return None


def build_github_bridge(
    agent_graph: dict[str, Any], githound_graphs: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    agent_nodes = _nodes(agent_graph)
    github_nodes = [node for graph in githound_graphs for node in _nodes(graph)]

    repositories = _unique_index(
        (
            (key, node)
            for node in github_nodes
            if _has_kind(node, "GH_Repository")
            for key in [_repository_key(node)]
            if key is not None
        ),
        "GH_Repository",
    )
    branches = _unique_index(
        (
            (
                (properties.get("repository_id"), properties.get("short_name")),
                node,
            )
            for node in github_nodes
            if _has_kind(node, "GH_Branch")
            for properties in [_properties(node)]
            if isinstance(properties.get("repository_id"), str)
            and isinstance(properties.get("short_name"), str)
        ),
        "GH_Branch",
    )

    assets = {
        _node_id(node): node
        for node in agent_nodes
        if _has_kind(node, "Asset", "AH_Asset")
        and _properties(node).get("asset_kind") == "SourceCode"
    }
    sources = [
        node for node in agent_nodes if _has_kind(node, "Source", "AH_Source")
    ]

    edges: list[dict[str, Any]] = []
    matched_assets: dict[str, dict[str, Any]] = {}
    for asset_id, asset in assets.items():
        properties = _properties(asset)
        remote = properties.get("git_remote_repository")
        if not isinstance(remote, str) or not remote:
            continue
        repository = repositories.get(remote.casefold())
        if repository is None:
            continue
        repository_id = _node_id(repository)
        matched_assets[asset_id] = repository
        edges.append({
            "start": _endpoint(asset_id),
            "end": _endpoint(repository_id, "GH_Repository"),
            "kind": "AH_CloneOf",
            "properties": {
                "traversable": False,
                "confidence": "confirmed",
                "evidence": f"Local Git remote matched GitHound repository {remote}",
            },
        })

    for source in sources:
        properties = _properties(source)
        source_asset_id = properties.get("git_repository_asset_id")
        if not isinstance(source_asset_id, str) or source_asset_id not in matched_assets:
            continue
        if properties.get("git_committed") is not True or properties.get("git_clean") is not True:
            continue

        asset_properties = _properties(assets[source_asset_id])
        branch_name = asset_properties.get("git_upstream_branch")
        head_commit = asset_properties.get("git_head_commit")
        if not isinstance(branch_name, str) or not isinstance(head_commit, str):
            continue
        repository_id = _node_id(matched_assets[source_asset_id])
        branch = branches.get((repository_id, branch_name))
        if branch is None:
            continue
        branch_properties = _properties(branch)
        branch_commit = branch_properties.get("commit_hash")
        if not isinstance(branch_commit, str) or branch_commit.casefold() != head_commit.casefold():
            continue

        relative_path = properties.get("git_relative_path")
        edges.append({
            "start": _endpoint(_node_id(branch), "GH_Branch"),
            "end": _endpoint(_node_id(source)),
            "kind": "AH_SuppliesSource",
            "properties": {
                "traversable": True,
                "confidence": "confirmed",
                "evidence": (
                    f"Clean committed source {relative_path} matched "
                    f"{branch_name}@{head_commit}"
                ),
            },
        })

    return {"graph": {"nodes": [], "edges": edges}}
