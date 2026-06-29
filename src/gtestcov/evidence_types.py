from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceHit(BaseModel):
    backend: str
    kind: str
    path: str
    line: int | None = None
    symbol: str = ""
    excerpt: str = ""
    confidence: str = "candidate"
    reason: str = ""


class EvidenceQuery(BaseModel):
    target: str = ""
    symbols: list[str] = Field(default_factory=list)
    limit: int = 80
