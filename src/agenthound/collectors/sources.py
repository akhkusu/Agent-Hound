"""Collect Source nodes and Influences edges from the workspace."""

from __future__ import annotations

from pathlib import Path

from agenthound.collectors.base import CollectionResult
from agenthound.discovery.workspace import find_source_files
from agenthound.models.edges import Edge
from agenthound.models.nodes import Agent, Source

# Instruction files a specific agent reads automatically. A file mapped here
# only influences its matching agents (confirmed); an unmapped instruction
# file (AGENTS.md, SYSTEM_PROMPT.md) is a cross-tool convention read by any
# agent. This stops e.g. .cursorrules from being shown as influencing Claude.
_INSTRUCTION_TARGETS: dict[str, set[str]] = {
    "CLAUDE.md": {"claude-desktop", "claude-code"},
    ".cursorrules": {"cursor"},
    "COPILOT-INSTRUCTIONS.md": {"vscode-copilot"},
    "GEMINI.md": {"gemini"},
}


def _influence(source: Source, agent: Agent) -> tuple[bool, str, str] | None:
    """Decide whether `source` influences `agent`, with confidence/evidence.

    Returns (connect, confidence, evidence) or None when there is no edge.
    """
    if source.source_kind == "AgentInstruction":
        targets = _INSTRUCTION_TARGETS.get(source.name)
        if targets is None:
            # Cross-tool instruction convention: any agent reads it.
            return True, "confirmed", f"instruction:{source.name}"
        if agent.name in targets:
            return True, "confirmed", f"instruction:{source.name}"
        return None
    # DocFile and other passive docs: the agent *may* read it (search, RAG,
    # manual reference), so the influence is a guess, not a fact.
    return True, "suspected", f"doc-file:{source.source_kind}"


def collect_sources(agent: Agent, workspace: Path, scope: str) -> CollectionResult:
    result = CollectionResult()

    if scope == "global":
        return result

    raw = find_source_files(workspace)
    seen: set[str] = set()

    for path_str, kind in raw:
        fpath = Path(path_str)
        source = Source(
            name=fpath.name,
            source_kind=kind,
            path=path_str,
            is_external=False,
        )
        if source.objectid not in seen:
            result.sources.append(source)
            seen.add(source.objectid)
        decision = _influence(source, agent)
        if decision is not None:
            _, confidence, evidence = decision
            result.edges.append(Edge(
                start=source.objectid,
                end=agent.objectid,
                kind="Influences",
                properties={"confidence": confidence, "evidence": evidence},
            ))

    return result
