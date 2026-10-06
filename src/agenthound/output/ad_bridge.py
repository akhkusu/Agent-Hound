"""Match observed Windows identities to SharpHound AD objects."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any


_SID = re.compile(r"S-1-(?:\d+-)+\d+")


def _ad_ids(archive: zipfile.ZipFile, category: str) -> set[str]:
    matches = [name for name in archive.namelist() if Path(name).name.lower().endswith(f"_{category}.json")]
    if not matches:
        raise ValueError(f"SharpHound ZIP has no {category}.json")
    identifiers: set[str] = set()
    for name in matches:
        data = json.loads(archive.read(name))
        for item in data["data"]:
            identifier = item.get("ObjectIdentifier")
            if isinstance(identifier, str) and _SID.fullmatch(identifier):
                identifiers.add(identifier)
    return identifiers


def build_ad_bridge(agent_graph: dict[str, Any], ad_zip: Path) -> dict[str, Any]:
    agents = [node for node in agent_graph["graph"]["nodes"] if "Agent" in node["kinds"] or "AH_Agent" in node["kinds"]]
    observed = [node for node in agents if node["properties"].get("runtime_owner_sid")]
    if len(observed) != 1:
        raise ValueError("Expected exactly one Agent with an observed Windows owner SID")
    agent = observed[0]
    properties = agent["properties"]
    with zipfile.ZipFile(ad_zip) as archive:
        user_ids = _ad_ids(archive, "users")
        computer_ids = _ad_ids(archive, "computers")

    def endpoint(identifier: str, kind: str | None = None) -> dict[str, str]:
        result = {"value": identifier, "match_by": "id"}
        if kind:
            result["kind"] = kind
        return result

    edges = []
    for edge_kind, sid_key, ad_kind, known_ids in (
        ("AH_RunsAs", "runtime_owner_sid", "User", user_ids),
        ("AH_RunsOn", "runtime_computer_sid", "Computer", computer_ids),
    ):
        sid = properties.get(sid_key)
        if sid is None:
            continue
        if not isinstance(sid, str) or not _SID.fullmatch(sid):
            raise ValueError(f"Invalid {sid_key} on Agent node")
        if sid not in known_ids:
            continue
        edges.append({
            "start": endpoint(agent["id"]),
            "end": endpoint(sid, ad_kind),
            "kind": edge_kind,
            "properties": {
                "confidence": "confirmed",
                "evidence": f"Observed Windows SID matched SharpHound {ad_kind} ObjectIdentifier",
                "runtime_observed_at": properties.get("runtime_observed_at"),
            },
        })
    return {"graph": {"nodes": [], "edges": edges}}
