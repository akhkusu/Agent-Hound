"""Stable implementation claims; neither advertisement nor access authorization."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Confidence = Literal["none", "suspected", "confirmed"]
Resource = Literal[
    "filesystem",
    "process",
    "network",
    "repository",
    "database",
    "secrets",
    "messaging",
    "cloud",
    "container",
    "kubernetes",
    "identity",
    "browser",
    "cicd",
    "issues",
    "knowledge",
    "observability",
    "productivity",
    "email",
]
Action = Literal[
    "read",
    "write",
    "execute",
    "navigate",
    "query",
    "search",
    "send",
    "commit",
    "push",
    "merge",
    "create",
    "delete",
    "manage",
    "administer",
    "trigger",
]


class CapabilityClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    resource: Resource
    action: Action
    confidence: Confidence
    evidence: str = Field(min_length=1, max_length=2000)
    scope: str = Field(min_length=1, max_length=1000)
    locality: Literal["local", "remote", "unknown"] = "unknown"
    readonly: bool
    source_url: str
    verification_status: Literal["verified", "experimental", "live_advertised"]
    tools: tuple[str, ...] = Field(min_length=1)

    @field_validator("source_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or (
                parsed.fragment
                and not re.fullmatch(r"L[0-9]+(?:-L[0-9]+)?", parsed.fragment)
            )
        ):
            raise ValueError(
                "evidence must be an HTTPS URL without credentials/query/fragment"
            )
        return value

    @model_validator(mode="after")
    def conservative_status(self) -> CapabilityClaim:
        if (
            self.verification_status == "experimental"
            and self.confidence == "confirmed"
        ):
            raise ValueError("experimental claims cannot be confirmed")
        if self.readonly and self.action in {
            "write",
            "send",
            "commit",
            "push",
            "merge",
            "create",
            "delete",
            "manage",
            "administer",
            "trigger",
            "execute",
        }:
            raise ValueError("mutating/execute action cannot be marked readonly")
        return self

    def flat_property(self) -> str:
        # A JSON string is an OpenGraph scalar, not a nested graph property.
        return self.model_dump_json()
