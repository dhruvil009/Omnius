# Contributing

Thanks for contributing to `omnius`.

Omnius is still in an early local-first phase, so the main priority is keeping the implementation reliable, inspectable, and aligned with the core loop: collect work, plan a bounded run, dispatch local agent sessions in isolated worktrees, and return reviewable artifacts to the human.

## Ground Rules

- Keep Omnius local-first. Do not add hosted services, remote queues, or opaque background automation unless there is an explicit decision to expand scope.
- Keep macOS and Linux support in mind. Scheduler behavior should stay grounded in `launchd` on macOS and `cron` on Linux.
- Preserve runner boundaries. Codex and Claude support should flow through the runner abstraction instead of hard-coding one provider into shared pipeline logic.
- Prefer small, reviewable changes over large speculative refactors.
- Keep documentation lean and current. Update docs when user-facing behavior changes, but do not add broad future-facing docs just to fill space.

## Development Setup

Prerequisites:

- Python 3.11 or newer
- Git
- A local `codex` or `claude` CLI when testing real worker execution
- `launchd` on macOS or `cron` on Linux when testing scheduler installation

Clone the repo and install Omnius with the setup script:

```bash
./omnius_setup.sh
omnius doctor
```

For local development without installing the console script, run commands with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python3 -m omnius --help
```

## Before Opening a PR

Run the full test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

For setup or packaging changes, also run the setup-related tests:

```bash
PYTHONPATH=src python3 -m unittest tests.test_setup_script tests.test_install
```

Check the relevant CLI help when changing commands or flags:

```bash
PYTHONPATH=src python3 -m omnius --help
PYTHONPATH=src python3 -m omnius install --help
PYTHONPATH=src python3 -m omnius task add --help
```

## Change Expectations

If you change behavior:

- add or update tests
- keep CLI help, README examples, and status or doctor output accurate
- update resource prompts or schemas when planner or worker contracts change
- avoid invoking real agent CLIs from automated tests unless the test is explicitly opt-in

If you change setup, scheduler, runtime locking, preflight, dispatch, or worktree behavior:

- prefer focused integration-style tests in `tests/`
- cover failure paths, not just happy paths
- keep uninstall, stop, recover, and dry-run behavior predictable
- avoid writing outside the configured Omnius workspace except where scheduler installation explicitly requires it

If you change runner support:

- keep provider-specific behavior inside `src/omnius/runners/`
- use fakes or test doubles for runner invocation tests
- preserve task-level runner overrides and the configured default runner behavior

## Commit Style

Prefer concise conventional-style commit messages such as:

- `fix: ...`
- `feat: ...`
- `docs: ...`
- `test: ...`

## Project Layout

- `src/omnius/cli.py` contains the command-line interface
- `src/omnius/config.py` contains config loading and validation
- `src/omnius/install.py` and `src/omnius/scheduler.py` contain setup and scheduler lifecycle behavior
- `src/omnius/runtime.py` contains pipeline lock, stop, and recovery behavior
- `src/omnius/preflight.py` contains run-safety checks
- `src/omnius/planner.py`, `src/omnius/dispatcher.py`, and `src/omnius/tasks.py` contain the core loop planning and task dispatch flow
- `src/omnius/runners/` contains Codex and Claude runner integrations
- `src/omnius/resources/` contains prompt templates and JSON schemas
- `tests/` contains unit and integration-focused behavior tests
- `assets/social/` contains promotional image assets
- `omnius_setup.sh` contains the install bootstrap script

## Scope Discipline

Good contributions for this stage:

- correctness fixes
- setup, scheduler, and preflight reliability improvements
- tighter tests around task queues, worktree isolation, runner invocation, logs, status, and recovery
- clear status, doctor, and error-reporting improvements
- lean documentation updates that match the codebase

Changes that should usually start with discussion first:

- hosted or cloud execution
- non-local task sources as the default workflow
- always-on unattended automation beyond the configured local scheduler
- new runner providers
- major architecture rewrites
- broad UX redesigns
- large post-MVP feature additions

## License

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in Omnius is licensed under the Apache License, Version 2.0.
