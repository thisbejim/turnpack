from pathlib import Path

from turnpack.io import build_manifest, read_jsonl, workload_sha256
from turnpack.model import WorkloadRecord

FIXTURE = Path(__file__).parent / "fixtures" / "workload.turnpack.jsonl"


def test_digest_and_manifest_are_stable() -> None:
    rows = read_jsonl(FIXTURE)
    records = [WorkloadRecord.from_dict(value) for _, value in rows]
    first = build_manifest(records)
    second = build_manifest(records)
    assert first == second
    assert first["workload_sha256"] == workload_sha256(records)
    assert first["records"] == 4
    assert first["sessions"] == 2
    assert first["duration_s"] == 0.2
