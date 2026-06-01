# Omnius

Omnius is a runner-agnostic overnight orchestration tool for local development. It plans work from a local task queue, dispatches isolated worker sessions against configured git repos, records journal artifacts, and produces a morning brief.

## Current Scope

`main` currently includes:

- fresh-checkout bootstrap via `./omnius_setup.sh`
- installed lifecycle commands: `install`, `install-cron`, `install-launchd`, `doctor`, `uninstall`
- workspace bootstrap and config loading
- local task and recurring task intake
- planner prompt/manifest generation with manifest validation
- sequential worker dispatch on per-task git worktrees
- circuit-breaker and pipeline-budget enforcement
- recurring-task state tracking and quarantine handling
- cost ledgering and day-prep brief generation
- `omnius status` surfaces for the latest recorded run
- runtime lock controls via `omnius stop` and `omnius recover`
- per-task `agent` overrides for local tasks (`codex` or `claude`)
- archival only after workers produce a durable artifact, such as a committed task branch or PR URL
- scheduler entries preserve `OMNIUS_HOME`, `TZ`, `OMNIUS_SCHEDULED`, and `PATH`
- preflight rejects dirty repos, merge/rebase state, unwritable runtime paths, and low disk space
- worker prompts require understand/implement/test/review/report phases and preserve report fields in status JSON

## Setup

From a fresh clone:

```bash
./omnius_setup.sh
```

`omnius_setup.sh` installs Omnius into the current user's Python site packages, bootstraps `~/.omnius`, creates `omnius.toml` if it does not exist, and installs the default scheduler backend for the current OS:

- macOS: `launchd`
- Linux: `cron`

The setup script also prints the verification command to use next. If the installed `omnius` shim is not on your shell `PATH` yet, it falls back to `python -m omnius doctor` with the same interpreter used during setup.

The canonical schedule source of truth is `~/.omnius/omnius.toml` via `pipeline_cron`.

## Installed Commands

Once installed, the main commands are:

```bash
omnius install
omnius doctor
omnius run
omnius status --json
omnius task list --json
omnius task show O00001
omnius task add --title "Task title" --repo repo-slug --body "Task body"
omnius task complete O00001
omnius task pending
omnius task recurring
omnius stop --dry-run
omnius recover
omnius uninstall
```

Advanced scheduler-specific entrypoints are also available:

```bash
omnius install-cron
omnius install-launchd
```

The default workspace is `~/.omnius`. Override it with `OMNIUS_HOME=/path/to/workspace`.

## Development

The package targets Python 3.11+ and uses the standard library `unittest` suite.

```bash
PYTHONPATH=src python3.13 -m unittest discover -s tests
```
