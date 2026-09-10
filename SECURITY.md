# Security

`turnpack` is a local workload tool, not a secure vault. Treat prompts,
responses, tool definitions, and request bodies as sensitive.

* `compile`, `verify`, `inspect`, and manifest generation never make network
  requests.
* `replay` is the only command that sends data over the network, and it runs
  only when explicitly invoked with `--base-url`.
* Compiler input headers named `Authorization`, `Proxy-Authorization`,
  `Cookie`, `Set-Cookie`, `X-API-Key`, or `Api-Key` are dropped. Add a runtime
  credential explicitly with `--header` when replaying.
* Responses are not written to disk by default. Replay results contain status,
  timing, byte count, and a SHA-256 digest rather than response bodies.
* The tool has no telemetry, analytics, hosted service, or background process.

The built-in checks are not a substitute for secret rotation or a dedicated
secret scanner. Do not publish a workload containing real prompts or keys.
