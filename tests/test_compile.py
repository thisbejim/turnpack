from pathlib import Path

import pytest

from turnpack.compile import CompileError, CompileOptions, compile_rows
from turnpack.io import read_jsonl

FIXTURES = Path(__file__).parent / "fixtures"


def test_compile_preserves_session_order_and_drops_credentials() -> None:
    rows = read_jsonl(FIXTURES / "raw_requests.jsonl")
    records, warnings = compile_rows(rows)

    assert len(records) == 4
    assert [record.session_id for record in records] == ["alpha", "beta", "alpha", "beta"]
    assert [record.turn for record in records] == [0, 0, 1, 1]
    assert [record.at for record in records] == [0.0, 0.05, 0.125, 0.2]
    assert records[0].request["headers"] == {"X-Trace": "alpha"}
    assert any("dropped sensitive header" in warning for warning in warnings)


def test_compile_supports_explicit_dot_paths() -> None:
    rows = [
        (
            1,
            {
                "event": {
                    "conversation": "s1",
                    "when": 10,
                    "payload": {"model": "m", "messages": []},
                    "endpoint": "/v1/responses",
                }
            },
        ),
    ]
    records, warnings = compile_rows(
        rows,
        CompileOptions(
            session_key="event.conversation",
            timestamp_key="event.when",
            body_key="event.payload",
            path_key="event.endpoint",
        ),
    )

    assert warnings == []
    assert records[0].session_id == "s1"
    assert records[0].request["path"] == "/v1/responses"
    assert records[0].request["body"]["model"] == "m"


def test_missing_session_and_time_are_deterministic_warnings() -> None:
    rows = [(1, {"body": {"model": "m", "messages": []}})]
    records, warnings = compile_rows(rows)

    assert records[0].session_id == "line-000001"
    assert records[0].at == 0.0
    assert any("missing session id" in warning for warning in warnings)
    assert any("missing timestamp" in warning for warning in warnings)


def test_compile_sanitizes_canonical_sensitive_headers() -> None:
    rows = [
        (
            1,
            {
                "schema": "turnpack/v1",
                "session_id": "s",
                "turn": 0,
                "at": 0,
                "request": {
                    "method": "POST",
                    "path": "/v1/chat/completions",
                    "body": {"model": "m", "messages": []},
                    "headers": {"Authorization": "Bearer leaked", "X-Trace": "safe"},
                },
            },
        )
    ]

    records, warnings = compile_rows(rows)
    assert records[0].request["headers"] == {"X-Trace": "safe"}
    assert any("dropped sensitive header" in warning for warning in warnings)


def test_compile_rejects_duplicate_explicit_turns() -> None:
    rows = [
        (
            line,
            {
                "session_id": "s",
                "turn": 0,
                "timestamp": line,
                "body": {"model": "m", "messages": []},
            },
        )
        for line in (1, 2)
    ]

    with pytest.raises(CompileError, match="duplicate-turn"):
        compile_rows(rows)
