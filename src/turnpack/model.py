"""Small, JSON-friendly models used by the turnpack library."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA = "turnpack/v1"

SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
    }
)


@dataclass(frozen=True, slots=True)
class WorkloadRecord:
    """One request in a deterministic workload cassette."""

    session_id: str
    turn: int
    at: float
    request: dict[str, Any]
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema": SCHEMA,
            "session_id": self.session_id,
            "turn": self.turn,
            "at": self.at,
            "request": self.request,
        }
        if self.meta:
            value["meta"] = self.meta
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> WorkloadRecord:
        return cls(
            session_id=value["session_id"],
            turn=value["turn"],
            at=value["at"],
            request=value["request"],
            meta=value.get("meta"),
        )


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A user-facing validation problem."""

    line: int
    code: str
    message: str

    def format(self) -> str:
        return f"line {self.line}: {self.code}: {self.message}"


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """Safe-by-default result metadata for one replayed request."""

    session_id: str
    turn: int
    status: int | None
    headers_ms: float | None
    duration_ms: float
    response_bytes: int
    response_sha256: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn": self.turn,
            "status": self.status,
            "headers_ms": self.headers_ms,
            "duration_ms": self.duration_ms,
            "response_bytes": self.response_bytes,
            "response_sha256": self.response_sha256,
            "error": self.error,
        }
