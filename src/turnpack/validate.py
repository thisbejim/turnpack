"""Validation rules for the versioned turnpack v1 format."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .model import SCHEMA, SENSITIVE_HEADERS, ValidationIssue


def validate_rows(rows: Sequence[tuple[int, Mapping[str, Any]]]) -> list[ValidationIssue]:
    """Return all structural issues instead of stopping at the first one."""

    issues: list[ValidationIssue] = []
    per_session: defaultdict[str, list[tuple[int, int, float]]] = defaultdict(list)
    seen: dict[tuple[str, int], int] = {}

    for line, value in rows:
        schema = value.get("schema")
        if schema != SCHEMA:
            issues.append(ValidationIssue(line, "schema", f"expected {SCHEMA!r}"))

        session_id = value.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            issues.append(ValidationIssue(line, "session-id", "must be a non-empty string"))
            session_key = f"<line:{line}>"
        else:
            session_key = session_id

        turn = value.get("turn")
        if not isinstance(turn, int) or isinstance(turn, bool) or turn < 0:
            issues.append(ValidationIssue(line, "turn", "must be a non-negative integer"))
            turn_value = -1
        else:
            turn_value = turn

        at = value.get("at")
        if (
            isinstance(at, bool)
            or not isinstance(at, (int, float))
            or not math.isfinite(float(at))
            or float(at) < 0
        ):
            issues.append(ValidationIssue(line, "timestamp", "at must be a finite number >= 0"))
            at_value = 0.0
        else:
            at_value = float(at)

        request = value.get("request")
        if not isinstance(request, dict):
            issues.append(ValidationIssue(line, "request", "must be a JSON object"))
            request = {}

        method = request.get("method")
        if not isinstance(method, str) or method.upper() != "POST":
            issues.append(ValidationIssue(line, "method", "request.method must be POST"))

        path = request.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            issues.append(ValidationIssue(line, "path", "request.path must start with '/'"))

        body = request.get("body")
        if not isinstance(body, dict):
            issues.append(ValidationIssue(line, "body", "request.body must be a JSON object"))

        headers = request.get("headers", {})
        if not isinstance(headers, dict):
            issues.append(ValidationIssue(line, "headers", "request.headers must be an object"))
        else:
            for name, header_value in headers.items():
                if not isinstance(name, str) or not isinstance(header_value, str):
                    issues.append(
                        ValidationIssue(line, "headers", "header names and values must be strings")
                    )
                elif name.lower() in SENSITIVE_HEADERS:
                    issues.append(
                        ValidationIssue(
                            line,
                            "sensitive-header",
                            f"{name!r} must be supplied at replay time, not stored in a cassette",
                        )
                    )

        meta = value.get("meta")
        if meta is not None and not isinstance(meta, dict):
            issues.append(ValidationIssue(line, "meta", "must be a JSON object when present"))

        if turn_value >= 0:
            key = (session_key, turn_value)
            if key in seen:
                issues.append(
                    ValidationIssue(
                        line,
                        "duplicate-turn",
                        f"session {session_key!r} turn {turn_value} is already on line {seen[key]}",
                    )
                )
            else:
                seen[key] = line
            per_session[session_key].append((turn_value, line, at_value))

    for session_id, entries in per_session.items():
        entries.sort(key=lambda item: item[0])
        expected = list(range(len(entries)))
        actual = [turn for turn, _, _ in entries]
        if actual != expected:
            first_line = entries[0][1]
            issues.append(
                ValidationIssue(
                    first_line,
                    "turn-gap",
                    f"session {session_id!r} turns must be contiguous from 0 (got {actual})",
                )
            )
        by_time = sorted(entries, key=lambda item: item[0])
        for (_, previous_line, previous_at), (_, current_line, current_at) in zip(
            by_time, by_time[1:], strict=False
        ):
            if current_at < previous_at:
                issues.append(
                    ValidationIssue(
                        current_line,
                        "timestamp-order",
                        f"at={current_at:g} is earlier than turn on line {previous_line}",
                    )
                )

    return issues


def ensure_valid(rows: Sequence[tuple[int, Mapping[str, Any]]]) -> None:
    """Raise ``ValueError`` with all validation failures, if any exist."""

    issues = validate_rows(rows)
    if issues:
        message = "\n".join(issue.format() for issue in issues)
        raise ValueError(message)
