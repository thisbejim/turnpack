# Product specification: turnpack

## Target developer

Inference engineers, model-platform engineers, and evaluation engineers who
need to compare an OpenAI-compatible server, model build, quantization, or
scheduler using realistic multi-turn traffic.

## Problem and job

When a serving stack changes, the engineer needs to replay the same sessions
with the same request bodies, ordering, and arrival timing so that latency,
throughput, and compatibility differences are attributable to the change.

The common workaround is to hand-convert production traces into the dataset
shape expected by a benchmark suite, regenerate synthetic assistant turns, or
write a one-off async script. Those paths can lose session state, headers,
timestamps, or exact request parameters.

## Public evidence

Independent projects expose the same recurring gap:

1. [GuideLLM #1024](https://github.com/vllm-project/guidellm/issues/1024)
   says saved multi-turn conversations cannot be loaded back, preventing
   replay across benchmark runs and wasting generation time on very long
   agentic workloads.
2. [genai-bench #194](https://github.com/sgl-project/genai-bench/issues/194)
   records that its single-turn request protocol lacks accumulated history,
   despite multi-turn being necessary for realistic chatbot and agent serving.
3. [EvalScope #1489](https://github.com/modelscope/evalscope/issues/1489)
   requests timestamp-driven production replay, per-request headers, request ID
   propagation, and fail-fast validation, while explicitly noting the absence
   of cross-request session state.
4. [vLLM #33640](https://github.com/vllm-project/vllm/issues/33640) documents
   reproducibility failures from tokenizer-dependent token counts and omitted
   API usage fields in multi-turn benchmarks.

The evidence is not a single feature request: it appears as separate issues in
benchmark engines from vLLM, SGLang, and ModelScope, plus concrete requirements
for preserving data and scheduling semantics.

## Existing workflow and alternatives

* **GuideLLM** offers rich profiles and trace replay, but its own conversation
  graph format and loader boundary make saved multi-turn data difficult to
  feed back in ([benchmark docs](https://github.com/vllm-project/guidellm/blob/main/docs/getting-started/benchmark.md)).
* **genai-bench** is a useful serving benchmark, but its issue describes a
  single-prompt protocol and backend-specific history handling.
* **EvalScope** is a broad evaluation system; its workload-trace proposal is
  intentionally open-loop/stateless and belongs inside that framework.
* **vLLM benchmark scripts** provide GPU-serving metrics, but the reproducibility
  issue shows that generated multi-turn data and token accounting are coupled
  to the runner.
* **Generic load tools** such as `wrk`, Locust, or k6 can schedule HTTP calls,
  but do not validate session/turn continuity or preserve an LLM request body
  as a reviewable cassette.

## Gap and thesis

> For inference and evaluation engineers, turnpack creates and verifies a
> portable multi-turn workload cassette better than benchmark-specific dataset
> adapters because it preserves exact JSON request bodies, session order,
> relative arrival timing, and a reproducible content hash without requiring a
> benchmark framework, model, account, or cloud service.

## Core workflow

```text
request-log JSONL
        ↓ turnpack compile
versioned turnpack/v1 JSONL + hash-only manifest
        ↓ verify / inspect
validated, reviewable workload
        ↓ replay --base-url (explicit)
safe latency/status result JSONL
```

## Non-goals

No GPU profiling, tokenization, model-quality judging, hosted dashboard,
automatic tool execution, provider-specific certification, or dynamic user
simulation. Replay is a deterministic transport primitive; larger benchmark
systems can consume its canonical file or result metadata.

## Interface and offline story

The primary interface is a dependency-free CLI with meaningful exit codes and
stdin/stdout support for Unix pipelines. Compile, verification, inspection, and
manifest generation work offline. Replay is opt-in network I/O and can be
previewed with `--dry-run`.

## Integration story

Any HTTP service accepting JSON `POST` requests can receive a cassette. The
default paths match common OpenAI-style `/v1/chat/completions` and
`/v1/responses` deployments; local vLLM, SGLang, Ollama, LM Studio, or a
compatible gateway can be selected with `--base-url`. Runtime auth is supplied
explicitly and is never serialized.

## Quality gate scores (0–10)

| Dimension | Score | Rationale |
| --- | ---: | --- |
| Developer pain | 8 | Lost turn history and regenerated workloads make comparisons unreliable. |
| Workflow frequency | 7 | Most acute for serving/eval teams, less so for single-prompt app developers. |
| Evidence of demand | 9 | Four independent issue trackers describe concrete replay/reproducibility gaps. |
| Repeated reinvention | 8 | Each benchmark engine is adding its own dataset and session-state variants. |
| Frontier-AI relevance | 9 | Long-context, agentic, multi-turn serving is a core inference concern. |
| Improvement over alternatives | 8 | A small stable cassette boundary decouples data preparation from runners. |
| Standalone usefulness | 8 | Verify/inspect/hash are valuable even without a target server. |
| Local-first advantage | 9 | Sensitive traces remain ordinary local files; no account or dashboard. |
| Search discoverability | 8 | “LLM workload replay”, “multi-turn benchmark”, and “OpenAI trace replay” are concrete jobs. |
| Technical feasibility | 9 | JSONL, hashing, scheduling, and HTTP are standard-library tasks. |
| Testability | 10 | Synthetic fixtures and an in-process HTTP server cover the full workflow. |
| Maintainability | 8 | Versioned format and intentionally narrow HTTP scope limit provider churn. |

The one below-8 score is frequency: the pain concentrates in teams operating
serving systems, not every AI application. That is a deliberate target rather
than a reason to broaden the tool into a generic benchmark framework.

## Final pre-build challenge

An engineer would clone this repository when a benchmark result needs to be
reproduced across a server build or model configuration and the original
multi-turn trace is trapped in an incompatible runner format. The repository
is not a shallow AI demo because its main value is deterministic validation,
content addressing, explicit transport behavior, and fixture-first tests; it
does not call an LLM or hide its semantics behind a hosted service.
