from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Candidate:
    name: str
    row_text: str
    table_title: str
    match_score: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TableData:
    title: str
    headers: list[str]
    rows: list[list[str]]
    frame_url: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchResult:
    query: str
    candidates: list[Candidate]
    selected: Candidate | None
    performance_tables: list[TableData]

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "candidates": [c.to_dict() for c in self.candidates],
            "selected": self.selected.to_dict() if self.selected else None,
            "performance_tables": [t.to_dict() for t in self.performance_tables],
        }
