"""Minimal, local-first HTTP replay for canonical turnpack records."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from math import ceil
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .model import ReplayResult, WorkloadRecord


class ReplayError(ValueError):
    """Raised for invalid replay configuration."""


@dataclass(frozen=True, slots=True)
class ReplayOptions:
    base_url: str
    concurrency: int = 1
    speed: float = 1.0
    timeout: float = 30.0
    headers: Mapping[str, str] | None = None


def _check_options(options: ReplayOptions) -> None:
    if not options.base_url or "://" not in options.base_url:
        raise ReplayError("base_url must be an http(s) URL")
    parsed = urlsplit(options.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ReplayError("base_url must be an http(s) URL")
    if options.concurrency < 1:
        raise ReplayError("concurrency must be >= 1")
    if options.speed <= 0:
        raise ReplayError("speed must be > 0")
    if options.timeout <= 0:
        raise ReplayError("timeout must be > 0")


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _read_body(response: Any) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def _send_one(record: WorkloadRecord, options: ReplayOptions) -> ReplayResult:
    request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    request_headers.update(record.request.get("headers", {}))
    request_headers.update(options.headers or {})
    body = json.dumps(record.request["body"], ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    request = Request(
        _url(options.base_url, record.request["path"]),
        data=body,
        headers=request_headers,
        method="POST",
    )
    started = time.perf_counter()
    headers_ms: float | None = None
    try:
        with urlopen(request, timeout=options.timeout) as response:
            headers_ms = (time.perf_counter() - started) * 1000
            response_bytes, response_sha = _read_body(response)
            duration_ms = (time.perf_counter() - started) * 1000
            return ReplayResult(
                session_id=record.session_id,
                turn=record.turn,
                status=int(response.status),
                headers_ms=headers_ms,
                duration_ms=duration_ms,
                response_bytes=response_bytes,
                response_sha256=response_sha,
            )
    except HTTPError as exc:
        headers_ms = (time.perf_counter() - started) * 1000
        response_bytes, response_sha = _read_body(exc)
        duration_ms = (time.perf_counter() - started) * 1000
        return ReplayResult(
            session_id=record.session_id,
            turn=record.turn,
            status=int(exc.code),
            headers_ms=headers_ms,
            duration_ms=duration_ms,
            response_bytes=response_bytes,
            response_sha256=response_sha,
            error=f"HTTP {exc.code}",
        )
    except (URLError, TimeoutError, OSError) as exc:
        duration_ms = (time.perf_counter() - started) * 1000
        reason = getattr(exc, "reason", exc)
        return ReplayResult(
            session_id=record.session_id,
            turn=record.turn,
            status=None,
            headers_ms=headers_ms,
            duration_ms=duration_ms,
            response_bytes=0,
            response_sha256=None,
            error=str(reason),
        )


def replay_records(records: Sequence[WorkloadRecord], options: ReplayOptions) -> list[ReplayResult]:
    """Replay sessions concurrently while preserving turn order within each session."""

    _check_options(options)
    sessions: defaultdict[str, list[WorkloadRecord]] = defaultdict(list)
    for record in records:
        sessions[record.session_id].append(record)
    for session_records in sessions.values():
        session_records.sort(key=lambda record: record.turn)

    started = time.perf_counter()

    def run_session(session_records: list[WorkloadRecord]) -> list[ReplayResult]:
        results: list[ReplayResult] = []
        for record in session_records:
            due = started + record.at / options.speed
            delay = due - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            results.append(_send_one(record, options))
        return results

    output: list[ReplayResult] = []
    with ThreadPoolExecutor(max_workers=options.concurrency) as pool:
        futures = [
            pool.submit(run_session, session_records) for session_records in sessions.values()
        ]
        for future in as_completed(futures):
            output.extend(future.result())
    output.sort(key=lambda result: (result.session_id, result.turn))
    return output


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """Nearest-rank percentile, stable for small fixture-sized samples."""

    if not values:
        return None
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, ceil(quantile * len(ordered)) - 1))
    return round(ordered[index], 3)


def summarize_results(results: Sequence[ReplayResult]) -> dict[str, Any]:
    """Return a JSON-safe summary suitable for CI and terminal output."""

    durations = [result.duration_ms for result in results]
    headers = [result.headers_ms for result in results if result.headers_ms is not None]
    status_counts = Counter(
        str(result.status) if result.status is not None else "error" for result in results
    )
    sessions = sorted({result.session_id for result in results})
    errors = sum(1 for result in results if result.error is not None)
    return {
        "records": len(results),
        "sessions": len(sessions),
        "errors": errors,
        "statuses": dict(sorted(status_counts.items())),
        "duration_ms": {
            "p50": percentile(durations, 0.50),
            "p95": percentile(durations, 0.95),
            "p99": percentile(durations, 0.99),
        },
        "headers_ms": {
            "p50": percentile(headers, 0.50),
            "p95": percentile(headers, 0.95),
        },
    }
