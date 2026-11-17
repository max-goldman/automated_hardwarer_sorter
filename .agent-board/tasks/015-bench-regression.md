# 015 benchmark mode + replay regression tests

Status: TODO
Dependencies: 011
Branch: `task/015-bench-regression` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/bench.py (new), src/hwsorter/cli.py (add `bench` subcommand and `replay --compare` only), tests/replay/ (new: golden fixtures + tests), tests/test_bench.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it or config.py; request changes via the task file).
`sorter bench <recording>`: full-speed replay, per-stage mean/p50/p95/p99/max ms per frame and per part, throughput vs the 8.3 ms/frame budget, pass/fail. `sorter replay <rec> --compare <decision_log>`: re-run and diff decisions (ignoring `timings`), exit non-zero on mismatch; this is the Mac vs Dell check. Golden regression: generate 2-3 short synthetic recordings deterministically at test time (or small committed fixtures under 5 MB total), produce golden decision logs, test that the current pipeline reproduces them. Provide `scripts/update_goldens.py` (allowed in scope) to regenerate intentionally.

- Accuracy guard for the ADOPTED budget plan: on a deterministic synthetic run of at least 60 parts (all six classes, plus a few near-miss out-of-scope parts), compare `frame_stride` 1 vs 2 and `max_feature_frames` 10 vs 20. Report accuracy, contamination, and the median and MAD of the key features per class. Pass if the defaults lose no more than 0.5 percentage points of accuracy and median feature error grows by no more than 0.002 in. Make this a `sorter bench --compare-strides` option plus a (marked slow) test.
- Bench results on the dev machine don't count for acceptance (that needs the Dell); label them with the CPU.

## Verification Output
(worker pastes each command, exit code, last ~50 lines)

## Review Notes
(reviewer only)
