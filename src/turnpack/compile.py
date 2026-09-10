"""Compile common request-log shapes into canonical turnpack records."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from .model import SCHEMA, SENSITIVE_HEADERS, WorkloadRecord
from .validate import validate_rows


class CompileError(ValueError):
    """Raised when a source row cannot be normalized safely."""


@dataclass(slots=True)
class CompileOptions:
    session_key: str | None = None
    timestamp_key: str | None = None
    body_key: str | None = None
    path_key: str | None = None


@dataclass(slots=True)
class _Pending:
    line: int
    session_id: str
    timestamp: float
    explicit_turn: int | None
    request: dict[str, Any]
    meta: dict[str, Any] | None


_SESSION_KEYS = ("session_id", "conversation_id", "conversationId", "trace_id", "traceId")
_TIME_KEYS = ("timestamp", "time", "created_at", "created", "ts")
_REQUEST_KEYS = ("request", "http_request", "payload", "call")
_BODY_KEYS = ("body", "json", "request_body", "requestBody")
_PATH_KEYS = ("path", "endpoint", "url", "uri")
_BODY_TRANSPORT_KEYS = {
    "method",
    "url",
    "uri",
    "path",
    "endpoint",
    "headers",
    "body",
    "json",
    "request",
    "timestamp",
    "time",
    "created_at",
    "created",
    "ts",
    "session_id",
    "conversation_id",
    "conversationId",
    "trace_id",
    "traceId",
    "turn",
    "turn_index",
    "meta",
}


def _get_path(value: Mapping[str, Any], path: str | None) -> Any:
    if not path:
        return None
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _first(value: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _timestamp(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a timestamp")
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        stripped = value.strip()
        try:
            parsed = float(stripped)
        except ValueError:
            iso = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
            parsed_dt = datetime.fromisoformat(iso)
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=UTC)
            parsed = parsed_dt.timestamp()
    else:
        raise ValueError("expected a number or ISO-8601 string")
    if not math.isfinite(parsed):
        raise ValueError("timestamp must be finite")
    return parsed


def _coerce_path(value: Any) -> str:
    if value is None:
        return "/v1/chat/completions"
    if not isinstance(value, str) or not value.strip():
        raise ValueError("endpoint/path must be a non-empty string")
    candidate = value.strip()
    if "://" in candidate:
        candidate = urlsplit(candidate).path or "/"
    if not candidate.startswith("/"):
        candidate = "/" + candidate
    return candidate


def _headers(value: Any, line: int, warnings: list[str]) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("headers must be an object")
    result: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise ValueError("header names and values must be strings")
        if raw_name.lower() in SENSITIVE_HEADERS:
            warnings.append(f"line {line}: dropped sensitive header {raw_name!r}")
            continue
        result[raw_name] = raw_value
    return result


def _extract_body(
    raw: Mapping[str, Any], request: Mapping[str, Any] | None, options: CompileOptions
) -> Any:
    if options.body_key:
        body = _get_path(raw, options.body_key)
        if body is not None:
            return body
    if request is not None:
        body = _first(request, _BODY_KEYS)
        if body is not None:
            return body
        if any(key in request for key in ("messages", "input", "prompt", "model")):
            return {key: value for key, value in request.items() if key not in _BODY_TRANSPORT_KEYS}
    body = _first(raw, _BODY_KEYS)
    if body is not None:
        return body
    if any(key in raw for key in ("messages", "input", "prompt", "model")):
        return {key: value for key, value in raw.items() if key not in _BODY_TRANSPORT_KEYS}
    return None


def _extract_row(
    raw: Mapping[str, Any], line: int, options: CompileOptions, warnings: list[str]
) -> _Pending:
    source_session = (
        _get_path(raw, options.session_key) if options.session_key else _first(raw, _SESSION_KEYS)
    )
    if source_session is None or not str(source_session).strip():
        session_id = f"line-{line:06d}"
        warnings.append(f"line {line}: missing session id; assigned {session_id}")
    else:
        session_id = str(source_session)

    source_time = (
        _get_path(raw, options.timestamp_key) if options.timestamp_key else _first(raw, _TIME_KEYS)
    )
    if source_time is None:
        timestamp = float(line - 1)
        warnings.append(f"line {line}: missing timestamp; assigned source order")
    else:
        try:
            timestamp = _timestamp(source_time)
        except ValueError as exc:
            raise CompileError(f"line {line}: timestamp: {exc}") from exc

    raw_request: Mapping[str, Any] | None = None
    if isinstance(raw.get("request"), Mapping):
        raw_request = raw["request"]
    else:
        for key in _REQUEST_KEYS[1:]:
            if isinstance(raw.get(key), Mapping):
                raw_request = raw[key]
                break

    body = _extract_body(raw, raw_request, options)
    if not isinstance(body, Mapping):
        raise CompileError(
            f"line {line}: body: expected an object under request.body, body, "
            "or an inline messages/input field"
        )

    transport = raw_request if raw_request is not None else raw
    path_value = (
        _get_path(raw, options.path_key) if options.path_key else _first(transport, _PATH_KEYS)
    )
    if path_value is None and transport is not raw:
        path_value = _first(raw, _PATH_KEYS)
    path = _coerce_path(path_value)
    method = transport.get("method", "POST")
    if not isinstance(method, str) or method.upper() != "POST":
        raise CompileError(f"line {line}: method: only POST request records are supported")

    header_value = transport.get("headers")
    if header_value is None and transport is not raw:
        header_value = raw.get("headers")
    request = {"method": "POST", "path": path, "body": dict(body)}
    cleaned_headers = _headers(header_value, line, warnings)
    if cleaned_headers:
        request["headers"] = cleaned_headers

    raw_turn = _first(raw, ("turn", "turn_index", "turnIndex"))
    if raw_turn is not None:
        if not isinstance(raw_turn, int) or isinstance(raw_turn, bool) or raw_turn < 0:
            raise CompileError(f"line {line}: turn: must be a non-negative integer")
        explicit_turn: int | None = raw_turn
    else:
        explicit_turn = None

    meta = raw.get("meta")
    if meta is not None and not isinstance(meta, dict):
        raise CompileError(f"line {line}: meta: must be an object")
    return _Pending(line, session_id, timestamp, explicit_turn, request, meta)


def compile_rows(
    rows: Sequence[tuple[int, Mapping[str, Any]]], options: CompileOptions | None = None
) -> tuple[list[WorkloadRecord], list[str]]:
    """Normalize source rows and return canonical records plus non-fatal warnings."""

    options = options or CompileOptions()
    warnings: list[str] = []
    pending: list[_Pending] = []
    for line, raw in rows:
        if raw.get("schema") == SCHEMA:
            try:
                record = WorkloadRecord.from_dict(dict(raw))
            except (KeyError, TypeError, ValueError) as exc:
                raise CompileError(f"line {line}: canonical record is incomplete: {exc}") from exc
            canonical_request = dict(record.request)
            cleaned_headers = _headers(canonical_request.get("headers"), line, warnings)
            if cleaned_headers:
                canonical_request["headers"] = cleaned_headers
            else:
                canonical_request.pop("headers", None)
            pending.append(
                _Pending(
                    line, record.session_id, record.at, record.turn, canonical_request, record.meta
                )
            )
        else:
            pending.append(_extract_row(raw, line, options, warnings))

    pending.sort(key=lambda item: (item.timestamp, item.line))
    by_session: dict[str, list[_Pending]] = {}
    for item in pending:
        by_session.setdefault(item.session_id, []).append(item)

    for session_id, items in by_session.items():
        if all(item.explicit_turn is None for item in items):
            for index, item in enumerate(items):
                item.explicit_turn = index
        else:
            used = {item.explicit_turn for item in items if item.explicit_turn is not None}
            next_turn = 0
            for item in items:
                if item.explicit_turn is None:
                    while next_turn in used:
                        next_turn += 1
                    item.explicit_turn = next_turn
                    used.add(next_turn)
                    warnings.append(
                        f"line {item.line}: missing turn; assigned {next_turn} "
                        f"in session {session_id!r}"
                    )

    first_timestamp = min((item.timestamp for item in pending), default=0.0)
    record_pairs = [
        (
            item.line,
            WorkloadRecord(
                session_id=item.session_id,
                turn=item.explicit_turn if item.explicit_turn is not None else 0,
                # Six decimal places are sub-millisecond enough for a replay schedule,
                # while avoiding binary-float noise when epoch timestamps are large.
                at=round(max(0.0, item.timestamp - first_timestamp), 6),
                request=item.request,
                meta=item.meta,
            ),
        )
        for item in pending
    ]
    record_pairs.sort(key=lambda pair: (pair[1].at, pair[1].session_id, pair[1].turn))
    records = [record for _, record in record_pairs]
    issues = validate_rows([(line, record.to_dict()) for line, record in record_pairs])
    if issues:
        raise CompileError("\n".join(issue.format() for issue in issues))
    return records, warnings
