"""Node models for Agent-Hound OpenGraph output."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, computed_field

SourceKind = Literal["WebPage", "GitHubIssue", "DocFile", "SlackThread", "Skill", "AgentInstruction"]
CapabilityKind = Literal["MCPServer", "MCPTool", "ShellHook", "Permission"]
AssetKind = Literal["SSHKey", "EnvFile", "AwsCredentials", "GCloudCredentials", "KubeConfig", "SourceCode"]
ImpactKind = Literal["Exfiltration", "SupplyChainContamination", "SystemTakeover"]


def _hash_id(prefix: str, *parts: str) -> str:
    content = "\x00".join(parts)
    h = hashlib.sha256(content.encode()).hexdigest()[:8]
    return f"{prefix}-{h}"


class Agent(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    platform: str
    config_path: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def objectid(self) -> str:
        return _hash_id("agent", self.name, self.config_path)

    def to_opengraph(self) -> dict[str, Any]:
        return {
            "id": self.objectid,
            "kinds": ["Agent"],
            "properties": {
                "name": self.name.upper(),
                "displayname": self.platform,
                "platform": self.platform,
                "config_path": self.config_path,
            },
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def objectid(self) -> str:
        return _hash_id("cap", self.name, self.cap_kind)

    def to_opengraph(self) -> dict[str, Any]:
        props: dict[str, Any] = {
            "name": self.name.upper(),
            "displayname": self.name,
            "cap_kind": self.cap_kind,
            "has_shell": self.has_shell,
        }
        if self.command is not None:
            props["command"] = self.command
        if self.transport is not None:
            props["transport"] = self.transport
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
