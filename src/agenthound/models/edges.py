"""Edge models for Agent-Hound OpenGraph output."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EdgeKind = Literal["Influences", "HasCapability", "CanAccess", "Triggers"]


class Edge(BaseModel):
    start: str
    end: str
    kind: EdgeKind
    # Carries confidence/evidence for the capability dimension that produced
    # the edge, so BloodHound can distinguish facts from name-based guesses.
    properties: dict[str, Any] = Field(default_factory=dict)

    def to_opengraph(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "start": {"value": self.start, "match_by": "id"},
            "end": {"value": self.end, "match_by": "id"},
            "kind": self.kind,
        }
        if self.properties:
            data["properties"] = dict(self.properties)
        return data
