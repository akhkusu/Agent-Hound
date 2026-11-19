"""Bounded terminal preview of the same paths exported to BloodHound."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from itertools import islice
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text


def attack_paths(payload: dict[str, Any]) -> Iterator[dict[str, str]]:
    nodes = {node["id"]: node for node in payload["graph"]["nodes"]}
    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    for edge in payload["graph"]["edges"]:
        outgoing[edge["start"]["value"]].append(edge)
        incoming[edge["end"]["value"]].append(edge)

    def label(key: str) -> str:
        props = nodes[key]["properties"]
        return str(props.get("displayname", props.get("name", key)))

    for agent_id, agent in nodes.items():
        if "Agent" not in agent["kinds"]:
            continue
        for capability in outgoing[agent_id]:
            if capability["kind"] != "HasCapability":
                continue
            cap_id = capability["end"]["value"]
            for target in outgoing[cap_id]:
                if target["kind"] not in {"CanAccess", "Triggers"}:
                    continue
                entries = (e for e in incoming[agent_id] if e["kind"] == "Influences")
                for entry in _with_default(entries):
                    chain = [capability, target] + ([entry] if entry else [])
                    confidence = "confirmed" if all(
                        e.get("properties", {}).get("confidence") == "confirmed" for e in chain
                    ) else "suspected"
                    props = target.get("properties", {})
                    yield {
                        "entry": f"{label(entry['start']['value'])} ({entry['kind']})" if entry else "Agent capability",
                        "agent": label(agent_id), "capability": label(cap_id),
                        "target": label(target["end"]["value"]),
                        "relation": target["kind"], "confidence": confidence,
                        "context": "explicit document context required" if props.get("requires_explicit_document_context")
                        else "metadata only" if props.get("access_scope") == "metadata" else props.get("retrieval_route", ""),
                    }


def _with_default(entries: Iterator[dict[str, Any]]) -> Iterator[dict[str, Any] | None]:
    first = next(entries, None)
    yield first
    if first is not None:
        yield from entries


def print_attack_paths(payload: dict[str, Any], console: Console, limit: int = 20) -> None:
    rows = list(islice(attack_paths(payload), limit + 1))
    table = Table(title="Attack Paths (potential reachability)")
    table.add_column("Agent / Capability", overflow="fold", ratio=2)
    table.add_column("Entry / Target", overflow="fold", ratio=3)
    table.add_column("Assessment", overflow="fold", ratio=2)
    for row in rows[:limit]:
        table.add_row(
            Text(f"{row['agent']}\n→ {row['capability']}"),
            Text(f"{row['entry']}\n→ {row['target']}"),
            Text(f"{row['relation']}\n{row['confidence']}\n{row['context']}"),
            end_section=True,
        )
    if rows:
        console.print(table)
        if len(rows) > limit:
            console.print(f"Showing first {limit} paths; full graph is in the output file.")
    else:
        console.print("Attack Paths: no capability-to-target paths evidenced.")
    agents = [n for n in payload["graph"]["nodes"] if "Agent" in n["kinds"]]
    if agents:
        console.print(Text("Agents: " + ", ".join(str(n["properties"].get("displayname", n["id"])) for n in agents)))
    console.print("Paths describe evidence-scoped reachability, not demonstrated exploitation.")
