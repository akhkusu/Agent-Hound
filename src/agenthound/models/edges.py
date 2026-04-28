"""Edge models for Agent-Hound OpenGraph output."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

EdgeKind = Literal["Influences", "HasCapability", "CanAccess", "Triggers"]


class Edge(BaseModel):
    start: str
    end: str
    kind: EdgeKind

    def to_opengraph(self) -> dict[str, Any]:
        return {
            "start": {"value": self.start, "match_by": "id"},
            "end": {"value": self.end, "match_by": "id"},
            "kind": self.kind,
        }
