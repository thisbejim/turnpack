# turnpack

Deterministic, provider-neutral cassettes for replaying multi-turn LLM
workloads.

Inference and evaluation engineers often need to run the *same* conversations
against a new server build, model, quantization, or scheduler. Existing
benchmark suites tend to own their own dataset shape, flatten conversation
history, or generate synthetic turns that cannot be saved and reused. That
makes a regression hard to reproduce and a performance comparison easy to
invalidate.

`turnpack` is a small local CLI and Python library that turns request logs into
a versioned JSONL cassette. It verifies session/turn continuity, preserves
inter-turn arrival times, produces a content hash, and can replay the cassette
to any explicitly selected HTTP endpoint. It does not need a model, account,
database, or hosted dashboard.

## Quick start

```text
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .

# Compile a request log into the canonical format.
turnpack compile tests/fixtures/raw_requests.jsonl -o workload.turnpack.jsonl

# Verify the cassette and inspect its shape (both are offline).
turnpack verify workload.turnpack.jsonl
turnpack inspect workload.turnpack.jsonl

# Preview the schedule without making a network request.
turnpack replay workload.turnpack.jsonl \
  --base-url http://127.0.0.1:8000 --dry-run

# Send it to an explicitly chosen OpenAI-compatible server.
turnpack replay workload.turnpack.jsonl \
  --base-url http://127.0.0.1:8000 --concurrency 2 \
  --header 'Authorization: Bearer local-dev-token' \
  --results replay.results.jsonl
```

The compiler writes `workload.turnpack.manifest.json` beside the cassette by
default. The manifest contains the record/session counts, endpoints, models,
relative duration, and SHA-256 digest; it intentionally contains no prompt
text.

Example verification output:

```text
valid: 4 records across 2 sessions
sha256: 5d5e424d…
```

Example replay output:

```text
alpha turn 0: 200 4.7ms, 38 bytes
alpha turn 1: 200 4.1ms, 38 bytes
beta turn 0: 200 4.5ms, 38 bytes
beta turn 1: 200 4.0ms, 38 bytes
summary: 4 requests, 0 errors, p95 4.7ms
```

## Why this exists

Public benchmark projects document the same missing primitive in different
ways:

* [GuideLLM issue #1024](https://github.com/vllm-project/guidellm/issues/1024)
  reports that serialized multi-turn conversations cannot be loaded back for
  reproducible runs.
* [genai-bench issue #194](https://github.com/sgl-project/genai-bench/issues/194)
  describes single-turn assumptions that prevent realistic multi-turn serving
  benchmarks.
* [EvalScope issue #1489](https://github.com/modelscope/evalscope/issues/1489)
  calls out production replay requirements such as timestamp scheduling,
  per-request headers, request IDs, and fail-fast validation, while noting that
  cross-request session state is not maintained.
* [vLLM issue #33640](https://github.com/vllm-project/vllm/issues/33640)
  shows how token-count and saved-output differences make multi-turn
  comparisons difficult to reproduce.

Those projects are excellent benchmark engines. `turnpack` deliberately does
not compete with their scheduling algorithms or GPU telemetry. It supplies a
portable, inspectable input contract that can be generated once, reviewed,
hashed, checked into an experiment, and replayed by a tiny deterministic
client or adapted into a larger harness.

## Supported inputs

`turnpack compile` accepts one JSON object per line. It understands either the
canonical format or common request-log shapes:

* session keys: `session_id`, `conversation_id`, `conversationId`, `trace_id`;
* timestamp keys: numeric seconds or ISO-8601 `timestamp`, `time`,
  `created_at`, `created`, `ts`;
* request bodies under `request.body`, `body`, `json`, `request_body`, or inline
  `messages`/`input`/`prompt`/`model` fields;
* endpoint paths or URLs under `path`, `endpoint`, `url`, or `uri`;
* optional explicit `turn`/`turn_index` values.

Use `--session-key`, `--timestamp-key`, `--body-key`, and `--path-key` for
other nested log shapes. Missing session IDs or timestamps are assigned stable
source-order values with warnings; use `--fail-on-warning` in CI when that is
not acceptable.

The canonical `turnpack/v1` record is:

```json
{
  "schema": "turnpack/v1",
  "session_id": "checkout-17",
  "turn": 1,
  "at": 0.250,
  "request": {
    "method": "POST",
    "path": "/v1/chat/completions",
    "body": {
      "model": "local-model",
      "messages": [{"role": "user", "content": "..."}]
    },
    "headers": {"X-Experiment": "candidate"}
  }
}
```

`at` is seconds from the earliest source event. Records are sorted by arrival
time, then session and turn, while replay always keeps turns within a session
sequential. Validation rejects duplicate turns, gaps, backwards per-session
timestamps, non-POST requests, non-object bodies, and sensitive stored headers.

## Commands

| Command | Purpose | Network |
| --- | --- | --- |
| `compile` | Normalize logs and write canonical JSONL + manifest | Never |
| `verify` | Validate the cassette and print its digest | Never |
| `inspect` | Show counts, duration, endpoints, and models | Never |
| `manifest` | Write a deterministic hash-only manifest | Never |
| `replay` | Schedule sessions and POST request bodies | Only when explicitly run |

`replay` reports status, header latency, total duration, response bytes, and a
response SHA-256. Response bodies are never printed or persisted by default.
Use `--results` for safe JSONL result metadata and `--json` for CI consumers.
`--dry-run` is useful for checking a workload in a disconnected environment.

## Integrations

The replay client sends ordinary JSON `POST` requests to a path in the cassette,
so it works with documented OpenAI-style endpoints exposed by local vLLM,
SGLang, Ollama, LM Studio, or a gateway. This is a protocol-level integration,
not an endorsement or a claim of complete provider compatibility. Runtime
credentials are supplied explicitly with `--header`; they are never carried
from source logs into a cassette.

The library is also intentionally easy to adapt:

```python
from turnpack import ReplayOptions, WorkloadRecord, replay_records

results = replay_records(
    [record],
    ReplayOptions(base_url="http://127.0.0.1:8000", concurrency=1),
)
```

## Privacy and security

All compile, verify, inspect, and manifest operations are offline. No telemetry
or background process exists. `replay` is the only network-capable command and
requires an explicit `--base-url`.

For safety, compiler input headers named `Authorization`, `Proxy-Authorization`,
`Cookie`, `Set-Cookie`, `X-API-Key`, and `Api-Key` are dropped. Add a local
credential at replay time instead. Treat prompts, tool definitions, and bodies
as sensitive, review cassettes before committing them, and rotate any real key
that may have appeared in a source log. See [SECURITY.md](SECURITY.md).

## Development

```text
python -m pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
mypy
```

Tests use only synthetic fixtures and a local in-process HTTP server. No API
keys, paid models, network access, or GPU dependencies are needed.

## Non-goals

`turnpack` is not a model runner, tokenizer, quality evaluator, GPU profiler,
cloud trace store, or general-purpose load-testing framework. It does not
execute tool calls from a recorded body, infer missing assistant responses, or
claim complete compatibility with any provider.

## License

MIT. See [LICENSE](LICENSE).
