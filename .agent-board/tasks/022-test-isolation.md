# 022 tests ignore machine-local config; measure polish

Status: DONE (merged)
Dependencies: 019, 021 merged
Branch: `task/022-test-isolation` (from `develop`)

## Why
On the rig Mac, `config/local.toml` (untracked, per machine) sets `camera.exposure_ms = 8.0` for flicker-free stationary tests. Tests that go through `cli.main()` load it, so `tests/replay/test_golden.py::test_replay_compare_matches_golden` and `::test_replay_compare_ignores_timings_and_machine` fail (`session_start[0].config.camera.exposure_ms: expected 0.5, got 8.0`). Tests must never depend on a machine's local config or calibration.

## Scope
`tests/conftest.py` (root), possibly `src/hwsorter/config.py` ONLY to add an env-var switch (e.g. `HWSORTER_NO_LOCAL_CONFIG=1`) that skips `config/local.toml` if no such hook exists — check how `HWSORTER_ROOT`/`HWSORTER_MACHINE` already work first and prefer reusing them (e.g. point HWSORTER_ROOT or the calibration/local paths at a temp dir in an autouse session fixture). `src/hwsorter/measure.py` for the polish items. Tests for both.

## Requirements
1. An autouse fixture makes every test run without `config/local.toml` and without the real machine calibration file, so the suite is identical on every machine. Add a test proving a local.toml with a different value does not leak into `load_config()`/`cli.main()` under tests. Real CLI use must still read local.toml.
2. measure.py: call `session.start()` inside the try so `stop()` always runs; list sporadic clusters (centre in inches, frames seen) in the printed report and in measure.json instead of only counting them.

## Verification
With `config/local.toml` present in the MAIN checkout containing `[camera]\nexposure_ms = 8.0` (copy it into your worktree's config/ for the run): `uv run pytest -q; echo "exit=$?"` fully green; ruff check + format --check exit 0.

## Verification Output
Worker (Sonnet), commit 22817cc, with local.toml (exposure 8.0) present: pytest exit=0; ruff exit=0; format exit=0. Isolation: tests/conftest.py pytest_configure points HWSORTER_ROOT at a temp root of symlinks without config/local.toml, config/calibration/ and output dirs. measure: start() inside try; sporadic clusters listed.

## Review Notes
Orchestrator self-review (small, test-only + polish), as in session 3: sound. Note: symlinks may need Developer Mode on Windows. Verified on merged develop below.
