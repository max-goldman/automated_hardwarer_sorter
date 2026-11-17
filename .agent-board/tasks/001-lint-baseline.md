# 001 Lint baseline

Status: DONE
Dependencies: none
Branch: `task/001-lint-baseline` (from `develop`)

## Goal
Make `uvx ruff@0.17.5 check src tests` and `uvx ruff@0.17.5 format --check src tests` pass on the repo, with a pinned config so results don't depend on any user-global ruff config.

## Scope (files the task may touch)
- `pyproject.toml` (add `[tool.ruff]` / `[tool.ruff.lint]` / `[tool.ruff.format]` only)
- Any file under `src/` and `tests/` ONLY for lint/format fixes. No behavior changes.

## Requirements
- Add to `pyproject.toml`: `line-length = 100`, `target-version = "py312"`, lint `select = ["E", "F", "W", "I", "UP", "B", "RUF", "PLW"]` (keep what is useful; you may `ignore` specific rules with a one-line comment why, e.g. RUF001-003 if unicode text is intentional). Pin so ruff does not read parent/user config.
- Fix the existing findings. Be careful with behavior-affecting ones:
  - RUF046 (`int(round(x))`): fine to change to `round(x)` only where x is a float and the result is used as an int.
  - B023 (loop variable captured in closure): fix with default-arg binding and make sure behavior is unchanged.
  - PLW1510 (`subprocess.run` without `check`): add explicit `check=False` if the current behavior ignores failures.
  - Do not use `--unsafe-fixes` blindly; review each.
- Run `uvx ruff@0.17.5 format src tests` once and commit the result.
- All 34 tests must still pass.

## Verification Output
Commit: `944d126` on `task/001-lint-baseline`. Worktree: `<REPO_ROOT>/.claude/worktrees/agent-a9484fa6ca2c60f63`

- `uv sync` -> exit 0
- `uv run pytest` -> exit 0
```
..................................                                       [100%]
34 passed in 11.06s
```
- `uvx ruff@0.17.5 check src tests` -> exit 0
```
All checks passed!
```
- `uvx ruff@0.17.5 format --check src tests` -> exit 0
```
31 files already formatted
```
- `uv run sorter --help` -> exit 0 (usage with subcommands camera-info, gui, record, replay, recordings, calibrate)

Rules ignored (in `[tool.ruff.lint] ignore`): E501 (formatter does not wrap long comments/strings; ~75 prose lines), RUF001/002/003 (intentional en dashes in GUI text).
Per-line noqa: PLW0603 and PLW0642 in `camera/avfoundation.py` (lazy ObjC delegate class global; PyObjC `self = objc.super(...).init()` idiom).

Non-purely-mechanical fixes (all behavior-preserving):
- RUF046: `int(round(x))` -> `round(x)` via unsafe fix, reviewed (16 sites, all float operands).
- B023 in `calibration/focus.py`: renamed reassigned `row` to `base` (also fixes PLW2901) and bound `base`/`peak` as default args of `crossing()`; closure is only called within the same iteration, so identical.
- PLW1510: explicit `check=False` (config.py, recording/store.py).
- B905: `strict=True` on zips in flatfield.py and tests/test_core.py (equal lengths; tests pass); RUF007: `itertools.pairwise` in test_core.py.
- tests/test_gui.py: unused names prefixed with `_`.
- `ruff format` applied once across src/tests (26 files).


## Review Notes
Commands re-run in worktree (commit 944d126): `uv run pytest` exit 0 (34 passed); `uvx ruff@0.17.5 check src tests` exit 0; `uvx ruff@0.17.5 format --check src tests` exit 0 (31 files); `uv run sorter --help` exit 0.

Findings from `git diff develop...task/001-lint-baseline`:
- Scope OK: only pyproject.toml (ruff sections only) plus src/ and tests/. Config is declared in pyproject `[tool.ruff]`, so it takes precedence over user-global config (ruff does not merge it). Ignores (E501, RUF001-003) are commented and reasonable.
- RUF046: 16 sites, operands are Python floats or int/float quotients (or numpy scalars where `round()` returns int); `round(x)` is identical to `int(round(x))`. No array operands.
- B023 focus.py: `row` -> `base` rename plus default-arg binding of `base`/`peak`. All uses of `row` after the reassignment were moved to `base`, and `crossing()` is only called in the same iteration, so behavior is identical.
- `check=False` additions match prior default behavior. `strict=True` zips (flatfield.py: frames/keep come from the same list, so equal length; test_core.py: tests pass). `pairwise` is equivalent.
- Import changes: typing -> collections.abc, `timezone.utc` -> `UTC`, removal of obsolete `noqa: E402`; no side-effect reordering. `noqa` comments for rules not selected (BLE001, N802, B027) were dropped, which is harmless.
- Remaining diff is formatter-only.

APPROVE
