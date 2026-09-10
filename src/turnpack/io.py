"""JSONL I/O and deterministic hashing for turnpack cassettes."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO, cast

from .model import SCHEMA, WorkloadRecord


class JsonlError(ValueError):
    """Raised when a JSONL input line cannot be read."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: invalid-json: {message}")
        self.line = line
        self.message = message


@contextmanager
def _open_text(path: str | Path, mode: str) -> Iterator[TextIO]:
    if str(path) == "-":
        if "r" in mode:
            yield cast(TextIO, sys.stdin)
        else:
            yield cast(TextIO, sys.stdout)
        return
    with Path(path).open(mode, encoding="utf-8", newline="") as handle:
        yield cast(TextIO, handle)


def read_jsonl(path: str | Path) -> list[tuple[int, dict[str, Any]]]:
    """Read non-empty JSON objects and retain source line numbers."""

    rows: list[tuple[int, dict[str, Any]]] = []
    with _open_text(path, "r") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise JsonlError(line_number, exc.msg) from exc
            if not isinstance(value, dict):
                raise JsonlError(line_number, "expected a JSON object")
            rows.append((line_number, value))
    return rows


def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically for hashes and stable diffs."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def workload_sha256(records: list[WorkloadRecord]) -> str:
    """Hash canonical record lines, including their newline separators."""

    digest = hashlib.sha256()
    for record in records:
        digest.update(canonical_json(record.to_dict()).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def write_records(path: str | Path, records: list[WorkloadRecord]) -> str:
    """Write canonical JSONL and return the exact workload digest."""

    digest = hashlib.sha256()
    with _open_text(path, "w") as handle:
        for record in records:
            line = canonical_json(record.to_dict())
            handle.write(line)
            handle.write("\n")
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def write_json(path: str | Path, value: Any) -> None:
    """Write a human-readable JSON document, or emit it to stdout."""

    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if str(path) == "-":
        sys.stdout.write(text)
    else:
        Path(path).write_text(text, encoding="utf-8")


def records_from_rows(rows: list[tuple[int, dict[str, Any]]]) -> list[WorkloadRecord]:
    """Convert already-validated canonical rows to models."""

    return [WorkloadRecord.from_dict(value) for _, value in rows]


def build_manifest(records: list[WorkloadRecord]) -> dict[str, Any]:
    """Build a deterministic, content-addressed workload manifest."""

    sessions = sorted({record.session_id for record in records})
    endpoints = sorted({record.request["path"] for record in records})
    models = sorted(
        {
            str(record.request["body"]["model"])
            for record in records
            if isinstance(record.request.get("body"), dict) and "model" in record.request["body"]
        }
    )
    last_at = max((record.at for record in records), default=0.0)
    return {
        "schema": "turnpack-manifest/v1",
        "workload_schema": SCHEMA,
        "workload_sha256": workload_sha256(records),
        "records": len(records),
        "sessions": len(sessions),
        "turns": len(records),
        "duration_s": last_at,
        "endpoints": endpoints,
        "models": models,
    }
