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
    ".cursorrules": {"cursor"},
    "GEMINI.md": {"gemini"},
    "AGENTS.override.md": {"codex"},
}


def _path_targets(
    source: Source, rel: tuple[str, ...] | None
) -> tuple[set[str], str] | None:
    if rel is None or len(rel) < 3:
        return None
    root: tuple[str, str] = (rel[0], rel[1])
    if source.source_kind == "Skill":
        targets = {
            (".agents", "skills"): {"codex", "vscode-copilot"},
            (".claude", "skills"): {"claude-code", "vscode-copilot"},
            (".github", "skills"): {"vscode-copilot"},
        }.get(root)
        return (targets, "skill") if targets else None
    if source.source_kind == "PromptFile":
        targets = {
            (".claude", "commands"): {"claude-code"},
            (".github", "prompts"): {"vscode-copilot"},
        }.get(root)
        return (targets, "prompt") if targets else None
    if source.source_kind == "AgentDefinition":
        targets = {
            (".claude", "agents"): {"claude-code"},
            (".github", "agents"): {"vscode-copilot"},
        }.get(root)
        return (targets, "agent-definition") if targets else None
    if source.source_kind == "AgentInstruction":
        targets = {
            (".claude", "rules"): {"claude-code"},
            (".cursor", "rules"): {"cursor"},
            (".github", "instructions"): {"vscode-copilot"},
        }.get(root)
        return (targets, "instruction") if targets else None
    return None


def _relative_parts(source: Source, workspace: Path) -> tuple[str, ...] | None:
    try:
        return Path(source.path).resolve().relative_to(workspace.resolve()).parts
    except (OSError, ValueError):
        return None


def _source_name(path: Path, kind: str, workspace: Path) -> str:
    try:
        rel = path.resolve().relative_to(workspace.resolve()).parts
    except (OSError, ValueError):
        rel = ()
    root = rel[:2]
    if kind == "Skill":
        return f"Skill: {path.parent.name}"
    if kind == "PromptFile":
        prompt_name = path.name.removesuffix(".prompt.md").removesuffix(".md")
        prefix = "Claude Command" if root == (".claude", "commands") else "Copilot Prompt"
        return f"{prefix}: {prompt_name}"
    if kind == "AgentDefinition":
        prefix = "Claude Agent" if root == (".claude", "agents") else "Copilot Agent"
        return f"{prefix}: {path.stem}"
    if kind == "AgentInstruction":
        if rel == (".github", "copilot-instructions.md"):
            return "Copilot Instructions"
        if root == (".claude", "rules"):
            return f"Claude Rule: {path.stem}"
        if root == (".cursor", "rules"):
            return f"Cursor Rule: {path.stem}"
        if root == (".github", "instructions"):
            instruction_name = path.name.removesuffix(".instructions.md")
            return f"Copilot Instruction: {instruction_name}"
    return path.name


def _influence(
    source: Source, agent: Agent, workspace: Path
) -> tuple[bool, str, str] | None:
    """Decide whether `source` influences `agent`, with confidence/evidence.

    Returns (connect, confidence, evidence) or None when there is no edge.
    """
    rel = _relative_parts(source, workspace)
    path_targets = _path_targets(source, rel)
    if path_targets is not None:
        targets, evidence_kind = path_targets
        if agent.name not in targets:
            return None
        return True, "suspected", f"{evidence_kind}:{'/'.join(rel or ())}"

    if source.source_kind == "AgentInstruction":
        if rel == (".github", "copilot-instructions.md"):
            if agent.name == "vscode-copilot":
                return True, "confirmed", "instruction:.github/copilot-instructions.md"
            return None
        if source.name == "CLAUDE.md":
            # Claude Code loads the project-root file automatically; nested
            # files are loaded only when it works in that subtree. VS Code
            # supports the root and .claude compatibility locations.
            if agent.name == "claude-code" and rel == ("CLAUDE.md",):
                return True, "confirmed", "instruction:CLAUDE.md"
            if agent.name == "vscode-copilot" and rel in {
                ("CLAUDE.md",), (".claude", "CLAUDE.md")
            }:
                return True, "confirmed", f"instruction:{'/'.join(rel)}"
            if agent.name == "claude-code":
                return True, "suspected", f"instruction:{source.name}"
            return None
        if source.name == "AGENTS.md" and agent.name == "vscode-copilot":
            # Root AGENTS.md is always-on in VS Code; nested support is
            # optional/experimental and therefore remains suspected.
            confidence = "confirmed" if rel == ("AGENTS.md",) else "suspected"
            return True, confidence, f"instruction:{source.name}"
        if source.name in {"AGENTS.md", "AGENTS.override.md"} and agent.name == "codex":
            if source.name == "AGENTS.md" and (Path(source.path).parent / "AGENTS.override.md").is_file():
                return None
            confidence = "confirmed" if rel in {("AGENTS.md",), ("AGENTS.override.md",)} else "suspected"
            return True, confidence, f"instruction:{source.name}; presence only, actual loading unverified"
        if source.name == "AGENTS.md":
            return None
        instruction_targets = _INSTRUCTION_TARGETS.get(source.name)
        if instruction_targets is None:
            return None
        if agent.name in instruction_targets:
            return True, "confirmed", f"instruction:{source.name}"
        return None
    # Other passive docs: the agent *may* read it (search, RAG,
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
            name=_source_name(fpath, kind, workspace),
            source_kind=kind,
            path=path_str,
            is_external=False,
        )
        if source.objectid not in seen:
            result.sources.append(source)
            seen.add(source.objectid)
        decision = _influence(source, agent, workspace)
        if decision is not None:
            _, confidence, evidence = decision
            result.edges.append(Edge(
                start=source.objectid,
                end=agent.objectid,
                kind="Influences",
                properties={"confidence": confidence, "evidence": evidence},
            ))

    return result
