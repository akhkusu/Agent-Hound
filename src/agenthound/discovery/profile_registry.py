"""Validated, offline data registry for exact MCP implementation identities."""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agenthound.models.claims import CapabilityClaim


class ProfileEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_url: str
    evidence: str = Field(min_length=1)

    @field_validator("source_url")
    @classmethod
    def valid_source(cls, value: str) -> str:
        return CapabilityClaim.valid_url(value)


class CapabilityProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    canonical_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    display_name: str = Field(min_length=1)
    maintainer: str = Field(min_length=1)
    category: str = Field(min_length=1)
    packages: tuple[str, ...] = ()
    package_ecosystems: dict[str, Literal["npm", "pypi"]] = Field(default_factory=dict)
    executables: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    containers: tuple[str, ...] = ()
    endpoints: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    compatibility_aliases: tuple[str, ...] = ()
    repository_url: str
    documentation_url: str
    verified_ref: str = Field(min_length=1)
    verified_date: date
    status: Literal["verified", "recognized_only", "experimental"]
    claims: tuple[CapabilityClaim, ...] = ()
    limitations: tuple[str, ...] = Field(min_length=1)
    evidence: str = Field(min_length=1, max_length=2000)
    scope_information: tuple[str, ...] = ()
    identity_sources: tuple[ProfileEvidence, ...] = Field(min_length=1)
    scoped_paths: bool = False
    repo_scoped: bool = False

    @field_validator("repository_url", "documentation_url")
    @classmethod
    def official_url(cls, value: str) -> str:
        return CapabilityClaim.valid_url(value)

    @model_validator(mode="after")
    def valid_profile(self) -> CapabilityProfile:
        if not any(
            (
                self.packages,
                self.executables,
                self.modules,
                self.containers,
                self.endpoints,
            )
        ):
            raise ValueError("profile needs at least one machine-readable identity")
        if set(self.packages) != set(self.package_ecosystems):
            raise ValueError("every package needs an explicit ecosystem")
        if self.status == "recognized_only" and self.claims:
            raise ValueError("recognized-only profiles cannot claim capabilities")
        if self.status == "verified" and not self.claims:
            raise ValueError("verified capability profile needs verified claims")
        if self.status == "experimental" and any(
            c.confidence == "confirmed" for c in self.claims
        ):
            raise ValueError("experimental profile cannot confirm claims")
        if self.status == "verified" and any(
            c.verification_status != "verified" for c in self.claims
        ):
            raise ValueError("static verified profile needs verified claims")
        for endpoint in self.endpoints:
            CapabilityClaim.valid_url(endpoint)
        for name in (
            *self.packages,
            *self.executables,
            *self.modules,
            *self.aliases,
            *self.compatibility_aliases,
        ):
            if not name or any(ch.isspace() for ch in name) or "\\" in name:
                raise ValueError("invalid identity")
        return self

    @property
    def package(self) -> str:
        """Legacy primary identity property."""
        return self.packages[0] if self.packages else self.canonical_id

    @property
    def note(self) -> str:
        return self.evidence

    def _legacy(self, key: str):
        from agenthound.discovery.profiles import legacy_fields

        return legacy_fields(self.claims)[key]

    @property
    def file_access(self):
        return self._legacy("file_access")

    @property
    def file_readonly(self):
        return self._legacy("file_readonly")

    @property
    def shell_exec(self):
        return self._legacy("shell_exec")

    @property
    def network_send(self):
        return self._legacy("network_send")

    @property
    def git_write(self):
        return self._legacy("git_write")


def pypi_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def validate_registry(records: list[dict]) -> tuple[CapabilityProfile, ...]:
    profiles = tuple(CapabilityProfile.model_validate(record) for record in records)
    ids: set[str] = set()
    identities: dict[tuple[str, str], str] = {}
    aliases: dict[str, str] = {}
    for profile in profiles:
        if profile.canonical_id in ids:
            raise ValueError(f"duplicate canonical ID: {profile.canonical_id}")
        ids.add(profile.canonical_id)
        for alias in (*profile.aliases, *profile.compatibility_aliases):
            previous = aliases.get(alias)
            if previous is not None:
                raise ValueError(f"duplicate alias: {alias}")
            aliases[alias] = profile.canonical_id
        entries = [
            (
                profile.package_ecosystems[p],
                pypi_name(p) if profile.package_ecosystems[p] == "pypi" else p,
            )
            for p in profile.packages
        ]
        entries.extend(("executable", p.lower()) for p in profile.executables)
        entries.extend(("module", p) for p in profile.modules)
        entries.extend(("container", p) for p in profile.containers)
        entries.extend(("endpoint", p) for p in profile.endpoints)
        entries.extend(
            ("alias", p) for p in (*profile.aliases, *profile.compatibility_aliases)
        )
        for key in entries:
            previous = identities.get(key)
            if previous is not None and previous != profile.canonical_id:
                raise ValueError(f"duplicate identity: {key}")
            identities[key] = profile.canonical_id
    for alias, owner in aliases.items():
        for (kind, identity), other in identities.items():
            if kind != "alias" and alias == identity and owner != other:
                raise ValueError(f"alias conflicts with identity: {alias}")
    return profiles


@lru_cache(maxsize=1)
def load_profiles() -> tuple[CapabilityProfile, ...]:
    data = (
        files("agenthound.discovery")
        .joinpath("data/mcp_profiles.json")
        .read_text(encoding="utf-8")
    )
    document = json.loads(data)
    if document.get("schema_version") != 1:
        raise ValueError("unsupported MCP profile registry schema")
    return validate_registry(document["profiles"])
