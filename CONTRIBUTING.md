# Contributing

Thanks for helping make workload replay boring and reproducible.

## Development setup

```text
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
mypy
```

The project intentionally has no runtime dependencies. Keep the core usable
with the Python standard library and add an optional dependency only when a
feature cannot be implemented responsibly without one.

## Changes

* Add or update fixture-first tests for every behavior change.
* Keep the on-disk format versioned and backwards-compatible within `v1`.
* Never add real prompts, credentials, customer data, or provider responses to
  fixtures.
* Do not make network access implicit in the library or CLI.

Please explain any format change in the pull request and update the schema
section of the README.
