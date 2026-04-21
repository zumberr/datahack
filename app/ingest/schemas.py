from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class RawDocument(BaseModel):
    """Tolerant input shape — accepts arbitrary extra fields from the scraper."""
    model_config = ConfigDict(extra="allow")

    raw: dict[str, Any]

    @classmethod
    def from_any(cls, data: dict[str, Any]) -> "RawDocument":
        return cls(raw=data)


class NormalizedDocument(BaseModel):
    url: str
    title: str
    category: str
    content: str
    source_hash: str
    warnings: list[str] = []

    def __hash__(self) -> int:
        return hash((self.url, self.source_hash))
