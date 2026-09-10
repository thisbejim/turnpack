import json
from pathlib import Path

from turnpack.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_compile_verify_and_inspect(tmp_path: Path, capsys: object) -> None:
    output = tmp_path / "compiled.jsonl"
    assert main(["compile", str(FIXTURES / "raw_requests.jsonl"), "-o", str(output)]) == 0
    assert output.exists()
    assert output.with_name("compiled.manifest.json").exists()

    assert main(["verify", str(output)]) == 0
    assert main(["inspect", str(output), "--json"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "valid: 4 records" in captured.out
    assert '"sessions": 2' in captured.out


def test_cli_dry_run_is_network_free(capsys: object) -> None:
    assert (
        main(
            [
                "replay",
                str(FIXTURES / "workload.turnpack.jsonl"),
                "--base-url",
                "http://127.0.0.1:1",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    value = json.loads(captured.out)
    assert value["dry_run"] is True
    assert len(value["records"]) == 4
