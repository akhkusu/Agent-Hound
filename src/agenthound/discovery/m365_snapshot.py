"""Validated metadata-only observations of M365 identities and documents."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _validate_timestamp(value: str) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Observation timestamp must include a timezone")
    return value


class TimedObservation(Observation):
    observed_at: str | None = None

    @field_validator("observed_at")
    @classmethod
    def validate_optional_timestamp(cls, value: str | None) -> str | None:
        return _validate_timestamp(value) if value is not None else None


class Principal(Observation):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal["user", "group", "site_group"]


class Membership(TimedObservation):
    member_id: str
    group_id: str
    evidence: str = Field(min_length=1)


class Grant(TimedObservation):
    principal_id: str
    roles: list[Literal["read", "write", "owner"]] = Field(min_length=1)
    inheritance: Literal["unknown", "direct", "inherited"] = "unknown"
    evidence: str = Field(min_length=1)


class Document(Observation):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    web_url: str
    classification: str = "unclassified"
    acl_complete: bool = False
    grants: list[Grant] = Field(default_factory=list)

    @field_validator("web_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Use a canonical HTTPS document URL without credentials, query or fragment"
            )
        return value


class DocumentReference(TimedObservation):
    document_id: str
    mode: Literal["configured", "retrieved"]
    evidence: str = Field(min_length=1)
    retrieval_route: Literal[
        "unknown", "explicit_document_context", "autonomous_retrieval",
    ] = "unknown"


class AgentBinding(TimedObservation):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    principal_id: str
    evidence: str = Field(min_length=1)
    references: list[DocumentReference] = Field(default_factory=list)


class M365Snapshot(Observation):
    schema_version: Literal[1]
    tenant_id: str = Field(min_length=1)
    observed_at: str
    evidence_source: Literal["sharepoint_ui", "graph_export", "synthetic"]
    principals: list[Principal]
    memberships: list[Membership] = Field(default_factory=list)
    documents: list[Document]
    agents: list[AgentBinding] = Field(default_factory=list)

    @field_validator("observed_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _validate_timestamp(value)

    @model_validator(mode="after")
    def validate_references(self) -> M365Snapshot:
        for ids in [
            [item.id for item in self.principals],
            [item.id for item in self.documents],
            [item.id for item in self.agents],
        ]:
            if len(set(ids)) != len(ids):
                raise ValueError("Duplicate IDs in snapshot collection")
        principals = {p.id: p for p in self.principals}
        docs = {d.id for d in self.documents}
        for membership in self.memberships:
            if (
                membership.member_id not in principals
                or membership.group_id not in principals
            ):
                raise ValueError("Membership references unknown principal")
            if principals[membership.group_id].kind == "user":
                raise ValueError("Membership target must be a group")
        for doc in self.documents:
            if any(g.principal_id not in principals for g in doc.grants):
                raise ValueError("Grant references unknown principal")
        for agent in self.agents:
            if (
                agent.principal_id not in principals
                or principals[agent.principal_id].kind != "user"
            ):
                raise ValueError("Agent binding requires an observed user principal")
            if any(r.document_id not in docs for r in agent.references):
                raise ValueError("Agent reference points to unknown document")
        return self
