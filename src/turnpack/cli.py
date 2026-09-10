"""Command-line interface for turnpack."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .compile import CompileError, CompileOptions, compile_rows
from .io import JsonlError, build_manifest, read_jsonl, records_from_rows, write_json, write_records
from .model import ReplayResult, WorkloadRecord
from .replay import ReplayError, ReplayOptions, replay_records, summarize_results
from .validate import validate_rows

VERSION = "0.1.0"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="turnpack",
        description="Compile, verify, inspect, and replay deterministic multi-turn LLM workloads.",
    )
    parser.add_argument("--version", action="version", version=f"turnpack {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser(
        "compile", help="normalize request logs into turnpack JSONL"
    )
    compile_parser.add_argument("input", help="source JSONL path, or '-' for stdin")
    compile_parser.add_argument(
        "-o", "--output", required=True, help="canonical JSONL path, or '-' for stdout"
    )
    compile_parser.add_argument("--manifest", help="manifest JSON path (default: beside output)")
    compile_parser.add_argument("--session-key", help="dot path for the session identifier")
    compile_parser.add_argument("--timestamp-key", help="dot path for the event timestamp")
    compile_parser.add_argument("--body-key", help="dot path for the request body")
    compile_parser.add_argument("--path-key", help="dot path for the endpoint/path")
    compile_parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="return an error when compiler warnings are emitted",
    )

    for name, help_text in (
        ("verify", "validate a canonical turnpack JSONL file"),
        ("inspect", "show workload shape and timing statistics"),
        ("manifest", "write a deterministic content manifest"),
    ):
        command_parser = subparsers.add_parser(name, help=help_text)
        command_parser.add_argument("input", help="canonical JSONL path, or '-' for stdin")
        command_parser.add_argument(
            "--json", action="store_true", help="emit machine-readable JSON"
        )
        if name == "manifest":
            command_parser.add_argument(
                "-o", "--output", required=True, help="manifest JSON path, or '-' for stdout"
            )

    replay_parser = subparsers.add_parser(
        "replay", help="send a workload to an explicit HTTP endpoint"
    )
    replay_parser.add_argument("input", help="canonical JSONL path")
    replay_parser.add_argument(
        "--base-url", required=True, help="HTTP(S) origin to receive requests"
    )
    replay_parser.add_argument(
        "--concurrency", type=int, default=1, help="maximum concurrent sessions (default: 1)"
    )
    replay_parser.add_argument(
        "--speed", type=float, default=1.0, help="replay speed multiplier (default: 1.0)"
    )
    replay_parser.add_argument(
        "--timeout", type=float, default=30.0, help="per-request timeout seconds"
    )
    replay_parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="NAME:VALUE",
        help="explicit header to add (repeatable)",
    )
    replay_parser.add_argument("--results", help="write safe result JSONL to this path")
    replay_parser.add_argument(
        "--dry-run", action="store_true", help="print schedule without making requests"
    )
    replay_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def _read_records(path: str) -> tuple[list[tuple[int, dict[str, Any]]], list[WorkloadRecord]]:
    rows = read_jsonl(path)
    issues = validate_rows(rows)
    if issues:
        raise ValueError("\n".join(issue.format() for issue in issues))
    return rows, records_from_rows(rows)


def _manifest_path(output: str) -> str | None:
    if output == "-":
        return None
    path = Path(output)
    return str(path.with_name(path.stem + ".manifest.json"))


def _print_issues(issues: Sequence[Any]) -> None:
    for issue in issues:
        print(issue.format(), file=sys.stderr)


def _compile(args: argparse.Namespace) -> int:
    rows = read_jsonl(args.input)
    options = CompileOptions(
        session_key=args.session_key,
        timestamp_key=args.timestamp_key,
        body_key=args.body_key,
        path_key=args.path_key,
    )
    records, warnings = compile_rows(rows, options)
    if warnings:
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        if args.fail_on_warning:
            print("error: compilation stopped because --fail-on-warning was set", file=sys.stderr)
            return 2

    output_sha = write_records(args.output, records)
    output_manifest = args.manifest or _manifest_path(args.output)
    if output_manifest:
        write_json(output_manifest, build_manifest(records))
    message_stream = sys.stderr if args.output == "-" or output_manifest == "-" else sys.stdout
    destination = "stdout" if args.output == "-" else args.output
    session_count = len({record.session_id for record in records})
    print(
        f"compiled {len(records)} records across {session_count} sessions to {destination}",
        file=message_stream,
    )
    print(f"workload sha256: {output_sha}", file=message_stream)
    if output_manifest:
        print(f"manifest: {output_manifest}", file=message_stream)
    return 0


def _verify(args: argparse.Namespace) -> int:
    rows = read_jsonl(args.input)
    issues = validate_rows(rows)
    if issues:
        if args.json:
            print(
                json.dumps(
                    {"valid": False, "issues": [asdict(issue) for issue in issues]}, indent=2
                )
            )
        else:
            _print_issues(issues)
        return 2
    records = records_from_rows(rows)
    manifest = build_manifest(records)
    if args.json:
        print(json.dumps({"valid": True, **manifest}, indent=2, sort_keys=True))
    else:
        print(f"valid: {len(records)} records across {manifest['sessions']} sessions")
        print(f"sha256: {manifest['workload_sha256']}")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    _, records = _read_records(args.input)
    manifest = build_manifest(records)
    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print(f"records: {manifest['records']}")
        print(f"sessions: {manifest['sessions']}")
        print(f"duration: {manifest['duration_s']:.3f}s")
        print(f"endpoints: {', '.join(manifest['endpoints']) or '(none)'}")
        print(f"models: {', '.join(manifest['models']) or '(unspecified)'}")
        print(f"sha256: {manifest['workload_sha256']}")
    return 0


def _manifest(args: argparse.Namespace) -> int:
    _, records = _read_records(args.input)
    value = build_manifest(records)
    write_json(args.output, value)
    if args.output != "-" and not args.json:
        print(f"manifest: {args.output}")
        print(f"sha256: {value['workload_sha256']}")
    return 0


def _parse_headers(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name, separator, header_value = value.partition(":")
        if not separator or not name.strip() or not header_value.strip():
            raise ReplayError(f"invalid --header {value!r}; expected NAME:VALUE")
        result[name.strip()] = header_value.strip()
    return result


def _dry_run(records: Sequence[WorkloadRecord], speed: float, as_json: bool) -> int:
    if speed <= 0:
        raise ReplayError("speed must be > 0")
    schedule = [
        {"session_id": record.session_id, "turn": record.turn, "at_s": round(record.at / speed, 6)}
        for record in records
    ]
    if as_json:
        print(json.dumps({"dry_run": True, "records": schedule}, indent=2))
    else:
        print("dry run (no network requests)")
        for item in schedule:
            print(f"{item['at_s']:>9.3f}s  {item['session_id']}  turn {item['turn']}")
    return 0


def _write_results(path: str, results: Sequence[ReplayResult]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        for result in results:
            handle.write(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def _replay(args: argparse.Namespace) -> int:
    _, records = _read_records(args.input)
    if args.dry_run:
        return _dry_run(records, args.speed, args.json)
    options = ReplayOptions(
        base_url=args.base_url,
        concurrency=args.concurrency,
        speed=args.speed,
        timeout=args.timeout,
        headers=_parse_headers(args.header),
    )
    results = replay_records(records, options)
    summary = summarize_results(results)
    if args.results:
        _write_results(args.results, results)
    if args.json:
        print(
            json.dumps(
                {"summary": summary, "results": [result.to_dict() for result in results]}, indent=2
            )
        )
    else:
        for result in results:
            status = str(result.status) if result.status is not None else "ERROR"
            suffix = f" ({result.error})" if result.error else ""
            print(
                f"{result.session_id} turn {result.turn}: {status} "
                f"{result.duration_ms:.1f}ms, {result.response_bytes} bytes{suffix}"
            )
        print(
            f"summary: {summary['records']} requests, {summary['errors']} errors, "
            f"p95 {summary['duration_ms']['p95']}ms"
        )
        if args.results:
            print(f"results: {args.results}")
    return 1 if summary["errors"] else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "compile":
            return _compile(args)
        if args.command == "verify":
            return _verify(args)
        if args.command == "inspect":
            return _inspect(args)
        if args.command == "manifest":
            return _manifest(args)
        if args.command == "replay":
            return _replay(args)
        parser.error(f"unknown command {args.command!r}")
    except (CompileError, JsonlError, ReplayError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2
