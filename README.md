# Omnius

Omnius is a runner-agnostic overnight orchestration tool for local development. It plans work from a local task queue, dispatches isolated worker sessions against configured git repos, records journal artifacts, and produces a morning brief.

## Current Scope

`main` currently includes:

- workspace bootstrap and config loading
- local task and recurring task intake
- planner prompt/manifest generation with manifest validation
- sequential worker dispatch on per-task git worktrees
- circuit-breaker and pipeline-budget enforcement
- recurring-task state tracking and quarantine handling
- cost ledgering and day-prep brief generation
- `omnius status` surfaces for the latest recorded run
- per-task `agent` overrides for local tasks (`codex` or `claude`)

## Usage

Omnius currently exposes two CLI entrypoints:

```bash
python3.13 -m omnius run
python3.13 -m omnius status --json
```

The default workspace is `~/.omnius`. Override it with `OMNIUS_HOME=/path/to/workspace`.

## Development

The package targets Python 3.11+ and uses the standard library `unittest` suite.

```bash
python3.13 -m unittest -v
```
