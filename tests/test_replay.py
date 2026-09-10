import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

from turnpack.io import read_jsonl, records_from_rows
from turnpack.replay import ReplayOptions, replay_records, summarize_results

FIXTURE = Path(__file__).parent / "fixtures" / "workload.turnpack.jsonl"


class _Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[tuple[str, dict[str, object], str | None]]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        self.__class__.seen.append((self.path, body, self.headers.get("Authorization")))
        response = json.dumps({"ok": True, "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        return


def test_replay_is_local_and_sequential_per_session() -> None:
    _Handler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rows = read_jsonl(FIXTURE)
        records = records_from_rows(rows)
        results = replay_records(
            records,
            ReplayOptions(
                base_url=f"http://127.0.0.1:{server.server_port}",
                concurrency=2,
                speed=1000,
                headers={"Authorization": "Bearer explicit-test-header"},
            ),
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert [result.status for result in results] == [200, 200, 200, 200]
    assert all(result.response_sha256 for result in results)
    assert all(result.response_bytes > 0 for result in results)
    assert len(_Handler.seen) == 4
    by_first_message: dict[str, list[int]] = {}
    for _, body, _ in _Handler.seen:
        messages = body["messages"]
        first_message = str(messages[0]["content"])
        by_first_message.setdefault(first_message, []).append(len(messages))
    assert sorted(by_first_message["What is 2 + 2?"]) == [1, 3]
    assert sorted(by_first_message["Name a primary color."]) == [1, 3]
    assert all(item[2] == "Bearer explicit-test-header" for item in _Handler.seen)


def test_summary_reports_percentiles_and_errors() -> None:
    rows = read_jsonl(FIXTURE)
    records = records_from_rows(rows)
    results = replay_records(
        records[:1],
        ReplayOptions(base_url="http://127.0.0.1:1", timeout=0.01),
    )
    summary = summarize_results(results)
    assert summary["records"] == 1
    assert summary["errors"] == 1
    assert summary["duration_ms"]["p95"] is not None
