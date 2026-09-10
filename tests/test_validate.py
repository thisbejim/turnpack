from pathlib import Path

from turnpack.io import read_jsonl, records_from_rows
from turnpack.validate import validate_rows

FIXTURE = Path(__file__).parent / "fixtures" / "workload.turnpack.jsonl"


def test_fixture_is_valid() -> None:
    rows = read_jsonl(FIXTURE)
    assert validate_rows(rows) == []
    assert len(records_from_rows(rows)) == 4


def test_validation_reports_duplicate_and_gap() -> None:
    rows = [
        (
            1,
            {
                "schema": "turnpack/v1",
                "session_id": "s",
                "turn": 0,
                "at": 0,
                "request": {"method": "POST", "path": "/v1/chat/completions", "body": {}},
            },
        ),
        (
            2,
            {
                "schema": "turnpack/v1",
                "session_id": "s",
                "turn": 2,
                "at": 1,
                "request": {"method": "POST", "path": "/v1/chat/completions", "body": {}},
            },
        ),
        (
            3,
            {
                "schema": "turnpack/v1",
                "session_id": "s",
                "turn": 2,
                "at": 2,
                "request": {"method": "POST", "path": "/v1/chat/completions", "body": {}},
            },
        ),
    ]

    codes = [issue.code for issue in validate_rows(rows)]
    assert "duplicate-turn" in codes
    assert "turn-gap" in codes


def test_sensitive_headers_are_rejected() -> None:
    rows = read_jsonl(FIXTURE)
    rows[0][1]["request"]["headers"] = {"Authorization": "Bearer no"}
    issues = validate_rows(rows)
    assert any(issue.code == "sensitive-header" for issue in issues)
