# 021 cross-machine decision compare (tolerant floats)

Status: DONE (merged)
Dependencies: none
Branch: `task/021-cross-machine-compare` (from `develop`)

## Why
The golden logs were generated on the session-3 Linux x86_64 box. On the Apple M5 Mac, 5 tests in `tests/replay/test_golden.py` fail: every output, class, rule, verdict, flag and record count is identical, but feature medians/scores differ in the 5th-8th decimal (largest seen: 2.1e-5 in `median.hull_solidity`; 1.6e-5 in `score`). `sorter replay --compare` uses the same byte-identical check, and it is THE planned Mac-vs-Dell acceptance tool (brief: "replay recordings made on the Mac there and confirm the decisions match"), so it would always report false mismatches across machines.

## Scope (files the task may touch)
`src/hwsorter/bench.py` (compare/diff helpers, `cmd_replay_compare`), `src/hwsorter/cli.py` (a `--float-tol` / `--strict` option on `replay --compare`, registration only), `tests/replay/test_golden.py`, `tests/test_bench*.py` if present, README (the `--compare` line), `docs/design.md` (one sentence). Do NOT regenerate the goldens and do not touch the pipeline.

## Requirements
- Decision fields compare EXACTLY: record kinds and counts, ids, output, rule, verdict, part_class, pose, flags, n_frames, on_seam, member ids, miss reasons, ref_seq, and any other non-float.
- Floats (feature medians/spreads, scores, positions in inches) compare with a tolerance: |a-b| <= abs_tol + rel_tol*|b|, defaults abs 1e-4 and rel 1e-4 (document why: cross-CPU libm/SIMD differences are ~1e-5). null/NaN must equal null/NaN. Integer ns times that are derived from floats (e.g. t_send_ns, t_ref_ns if derived) allow +-1000 ns; integer frame times taken straight from the recording stay exact. Decide per key and list the rules in a docstring.
- `replay --compare` uses the tolerant compare by default and prints the largest float deviation seen; `--strict` keeps the byte-identical behaviour. Exit 1 on any decision mismatch.
- The golden tests: replace the byte-for-byte assertion with the tolerant compare, and keep a separate test that the tolerant compare FAILS when an output/class differs and when a float differs by more than the tolerance.
- All 5 currently failing golden tests must pass on this Mac with the committed goldens unchanged.

## Verification (paste each command, exit code, last ~50 lines into your final report)
`uv run pytest; echo "exit=$?"` (must be fully green), `uvx ruff@0.17.5 check src tests; echo "exit=$?"`, `uvx ruff@0.17.5 format --check src tests; echo "exit=$?"`.

## Verification Output
Worker (Sonnet), commit cd6867f, on the Apple M5: `uv run pytest` exit=0 (303 passed, 3 skipped, 1 xfailed); ruff check exit=0; format --check exit=0 (83 files). Demo: `replay --compare` on mixed_classes -> MATCH, max float deviation 2.1e-05, exit 0; with `--strict` -> MISMATCH, exit 1.
Rules: exact for bool/str/None/keys/list lengths/ints; floats |a-b| <= 1e-4 + 1e-4*|b|, NaN==NaN; t_send_ns/t_ref_ns/t_closed_ns +-1000 ns. CLI: `--float-tol X`, `--strict`.

## Review Notes
Round 1 (fresh Sonnet reviewer): APPROVE. Re-ran pytest exit=0, ruff exit=0, format exit=0. OPTIONAL: int-vs-float compares tolerantly (harmless today); no end-to-end CLI test of `--compare`/`--strict`.
