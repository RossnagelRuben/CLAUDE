# Repository Guidelines

## Project Structure & Module Organization
Core logic lives in `miniverse/` (orchestrator, cognition, perception, persistence, schemas). Tests mirror that layout in `tests/`, providing focused `pytest` suites per subsystem. Simulation demos sit in `examples/`, where the workshop progression spans deterministic through LLM-driven agents. Deep dives and research notes are collected in `docs/`; build outputs gather in `dist/` and experiment traces in `runs/`.

## Build, Test, and Development Commands
Use `uv sync` to install dependencies into the pinned environment (`uv.lock`). Run `uv run pytest` for the async test matrix, or narrow with `uv run pytest tests/test_orchestrator.py -k happy_path`. Execute scenarios via `uv run python -m examples.workshop.run --llm` or other module paths. During iteration, `uv run python -m miniverse.orchestrator` helps validate wiring and logging output.

## Coding Style & Naming Conventions
Code targets Python 3.10+, leaning on type hints and Pydantic models; keep new APIs typed and validated. Prefer concise module-level docstrings and inline comments only where agent logic is subtle. Use snake_case for callables, PascalCase for classes, and UPPER_SNAKE_CASE for constants or log tags. Maintain four-space indentation, keep lines near 100 characters, and favor explicit imports over star patterns.

## Testing Guidelines
Unit tests lean on `pytest` and `pytest-asyncio`; mirror new features with async-aware fixtures whenever side effects cross ticks. Name files `test_<module>.py` and build fixtures that cover both deterministic and LLM-backed flows. When extending cognition or persistence, add assertions for logging tags and schema validation. Run the full suite before submitting and keep assertions in `tests/`, not examples.

## Commit & Pull Request Guidelines
Write concise, imperative commit subjects ("Fix orchestrator error handling"), optionally prefixed with a scope such as `feat:` for new capabilities. Bundle related changes and document breaking API shifts in `CHANGELOG.md`. Pull requests should describe the scenario or subsystem touched, link issues from `ISSUES.md`, and cite `uv run` commands or logs that prove the behavior.

## Environment & Configuration Tips
Copy `.env.example` to `.env` when experimenting with LLM cognition, and set `LLM_PROVIDER`, `LLM_MODEL`, and API keys before launching examples. Use `DEBUG_LLM=1`, `DEBUG_MEMORY=1`, or `MINIVERSE_VERBOSE=1` to surface prompts and memory traces. Keep secrets and run artifacts out of commits; leave them in `.env` or the gitignored `runs/` folder.
