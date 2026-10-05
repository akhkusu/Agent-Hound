"""Node models for Agent-Hound OpenGraph output."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

SourceKind = Literal["WebPage", "GitHubIssue", "DocFile", "SlackThread", "Skill", "AgentInstruction"]
CapabilityKind = Literal["MCPServer", "MCPTool", "ShellHook", "Permission", "BuiltInTool"]
AssetKind = Literal["SSHKey", "EnvFile", "AwsCredentials", "GCloudCredentials", "KubeConfig", "SourceCode"]
ImpactKind = Literal["Exfiltration", "SupplyChainContamination", "SystemTakeover"]

# How sure we are that a capability dimension really exists:
# "confirmed" = derived from a fact in the config (known package, shell binary,
# hook definition), "suspected" = name-based heuristic only.
Confidence = Literal["none", "suspected", "confirmed"]


def _hash_id(prefix: str, *parts: str) -> str:
    content = "\x00".join(parts)
    h = hashlib.sha256(content.encode()).hexdigest()[:8]
    return f"{prefix}-{h}"


class Agent(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    platform: str
    config_path: str
    runtime_pid: int | None = None
    runtime_owner_sid: str | None = None
    runtime_observed_at: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def objectid(self) -> str:
        return _hash_id("agent", self.name, self.config_path)

    def to_opengraph(self) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "name": self.name.upper(),
            "displayname": self.platform,
            "platform": self.platform,
            "config_path": self.config_path,
        }
        if self.runtime_pid is not None and self.runtime_owner_sid is not None:
            properties.update(
                runtime_pid=self.runtime_pid,
                runtime_owner_sid=self.runtime_owner_sid,
                runtime_observed_at=self.runtime_observed_at,
                runtime_identity_evidence="Win32_Process.GetOwnerSid for selected claude.exe PID",
            )
        return {
            "id": self.objectid,
            "kinds": ["Agent"],
            "properties": properties,
        }


class Source(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    source_kind: SourceKind
    path: str
    is_external: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def objectid(self) -> str:
        return _hash_id("source", self.source_kind, self.path)

    def to_opengraph(self) -> dict[str, Any]:
        return {
            "id": self.objectid,
            "kinds": ["Source"],
            "properties": {
                "name": self.name.upper(),
                "displayname": self.name,
                "source_kind": self.source_kind,
                "path": self.path,
                "is_external": self.is_external,
            },
        }


class Capability(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    cap_kind: CapabilityKind
    command: str | None = None
    transport: str | None = None
    has_shell: bool = False
    # Identity of the owning agent (its objectid). Included in this cap's
    # objectid so that a "filesystem" server in two different agents stays two
    # distinct nodes — otherwise their scopes/capabilities would cross-
    # contaminate. Empty for directly-constructed caps, keeping legacy
    # objectids stable.
    agent_scope: str = ""
    # Per-dimension confidence: evidence differs per dimension (a package hit
    # proves file access but says nothing about shell), so one flag per axis.
    shell_exec: Confidence = "none"
    file_access: Confidence = "none"
    network_send: Confidence = "none"
    git_write: Confidence = "none"
    file_readonly: bool = False
    # None means "no known restriction" (scope unknown), a tuple restricts
    # file access to those directories (e.g. server-filesystem args).
    allowed_paths: tuple[str, ...] | None = None
    # For git capabilities: the repositories the server operates on (e.g.
    # server-git --repository). None means unknown / any repo.
    repo_scope: tuple[str, ...] | None = None
    shell_evidence: str = ""
    file_evidence: str = ""
    network_evidence: str = ""
    git_evidence: str = ""
    read_config_path: str = ""
    read_permissions: dict[str, Any] = Field(default_factory=dict)
    read_policy_sources: list[dict[str, Any]] = Field(default_factory=list)
    read_workspace: str = ""
    mcp_config_path: str = ""
    mcp_scope: str = ""
    mcp_evidence: str = ""

    @model_validator(mode="before")
    @classmethod
    def _sync_shell_fields(cls, data: Any) -> Any:
        # has_shell predates shell_exec; keep both coherent so legacy
        # constructors (hooks, tests) and new tri-state code agree.
        if isinstance(data, dict):
            if data.get("has_shell") and data.get("shell_exec", "none") == "none":
                data["shell_exec"] = "confirmed"
            elif data.get("shell_exec", "none") != "none":
                data["has_shell"] = True
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def objectid(self) -> str:
        return _hash_id("cap", self.name, self.cap_kind, self.agent_scope)

    def to_opengraph(self) -> dict[str, Any]:
        props: dict[str, Any] = {
            "name": self.name.upper(),
            "displayname": self.name,
            "cap_kind": self.cap_kind,
            "has_shell": self.has_shell,
            "shell_exec": self.shell_exec,
            "file_access": self.file_access,
            "network_send": self.network_send,
            "git_write": self.git_write,
            "file_readonly": self.file_readonly,
        }
        if self.command is not None:
            props["command"] = self.command
        if self.mcp_config_path:
            props.update(config_path=self.mcp_config_path, mcp_scope=self.mcp_scope,
                         evidence=self.mcp_evidence)
        if self.transport is not None:
            props["transport"] = self.transport
        if self.allowed_paths is not None:
            props["allowed_paths"] = list(self.allowed_paths)
        if self.repo_scope is not None:
            props["repo_scope"] = list(self.repo_scope)
        for key, value in (
            ("shell_evidence", self.shell_evidence),
            ("file_evidence", self.file_evidence),
            ("network_evidence", self.network_evidence),
            ("git_evidence", self.git_evidence),
        ):
            if value:
                props[key] = value
        return {"id": self.objectid, "kinds": ["Capability"], "properties": props}


class Asset(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    asset_kind: AssetKind
    path: str
    readable: bool = True
    writable: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def objectid(self) -> str:
        return _hash_id("asset", self.asset_kind, self.path)

    def to_opengraph(self) -> dict[str, Any]:
        return {
            "id": self.objectid,
            "kinds": ["Asset"],
            "properties": {
                "name": self.name.upper(),
                "displayname": self.name,
                "asset_kind": self.asset_kind,
                "path": self.path,
                "readable": self.readable,
                "writable": self.writable,
            },
        }


class Impact(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    impact_kind: ImpactKind
    reachable: bool = True
    description: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def objectid(self) -> str:
        return _hash_id("impact", self.impact_kind, self.name)

    def to_opengraph(self) -> dict[str, Any]:
        return {
            "id": self.objectid,
            "kinds": ["Impact"],
            "properties": {
                "name": self.name.upper(),
                "displayname": self.name,
                "impact_kind": self.impact_kind,
                "reachable": self.reachable,
                "description": self.description,
            },
        }
