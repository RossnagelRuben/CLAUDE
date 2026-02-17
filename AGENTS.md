# Repository Guidelines

## Project Structure & Module Organization
Core engine code lives in `miniverse/` (orchestrator, cognition, memory, persistence, schemas). Tests mirror module boundaries in `tests/`. Example/tutorial content lives in `examples/`. File-driven demo assets live in `demo/` (singular), not `demos/`, and should remain script + scenario driven (no demo-specific CLI command forks).

## Run & Development Commands
Use `uv sync` to install pinned deps (`uv.lock`). Main CLI entrypoint is:

- `uv run miniverse run <scenario-or-file> --ticks <N> [--llm] [--verbose]`
- `uv run miniverse list`
- `uv run miniverse info <scenario>`

Run tests with `uv run pytest` (or targeted subsets).

## Scenario Authoring
Scenarios are YAML-first for readability, with JSON still supported for compatibility. Use `ScenarioLoader`/`scenario_files` helpers rather than hand-rolled parsers. Keep scenario-defining content in files; avoid embedding scenario copy as hardcoded CLI print text.
Use `metadata.runtime` in scenario YAML to wire optional scenario-local extensions (`rules.py`, `cognition.py`) instead of hardcoded CLI branches.

## Logging Expectations
CLI behavior should be consistent across all runs:

- Deterministic default: final summary only.
- Deterministic verbose: structured tick timeline.
- LLM default: concise setup + pipeline/action progress.
- LLM verbose: memories, planning, reflection, and LLM response summaries.
- Debug flags (`DEBUG_*`) are for deep diagnostics and may be noisy.

Do not introduce demo-only log tags or branches into general CLI paths.

## Coding Style & Naming Conventions
Python 3.10+, typed APIs, Pydantic-first schema boundaries. Use snake_case for functions/modules, PascalCase for classes, UPPER_SNAKE_CASE for constants. Keep comments focused on non-obvious logic and preserve readable, low-noise output formatting.

## Testing Guidelines
Use `pytest` + `pytest-asyncio`. Add/update tests when changing:
- scenario loading/resolution
- cognition/logging behavior
- CLI output mode behavior

Before finishing, run the relevant test subset and at least one real CLI run for the touched mode(s).

## Environment & Configuration Tips
For LLM runs, set `LLM_PROVIDER`, `LLM_MODEL`, and provider API keys. `UV_CACHE_DIR=.uv-cache` is optional and only controls where uv stores cache files (helpful to keep project-local cache and avoid global cache churn).
