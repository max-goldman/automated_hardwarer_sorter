# 020 stationary-belt mode for `sorter run`

Status: IN_PROGRESS
Dependencies: none (M2/M3 merged)
Branch: `task/020-stationary-mode` (from `develop`)
Reviewer: Opus (tracker/pipeline/decision semantics).

## Why
User (2026-11-17): live tests start on a STATIONARY belt (parts placed by hand, belt not moving), then a moving belt soon after. As built, `belt.speed_in_s` must be > 0, the tracker counts an observation valid only when the part moves at belt speed, a track finishes only when it leaves the view (or is missed), groups close only when belt motion brings empty belt into view, and send times divide by belt speed. With a stopped belt nothing is ever decided correctly.

## Scope (files the task may touch)
`src/hwsorter/config.py` + `config/default.toml` (new `belt.mode = "moving" | "stationary"` and a small `[stationary]` section if needed — this is an approved schema change), `src/hwsorter/pipeline.py`, `src/hwsorter/vision/track.py` (stationary validity/finish rules only), `src/hwsorter/cli.py` (`--stationary` flag on `run` that sets belt.mode, registration only), `src/hwsorter/gui/` overlay only if needed to show stationary decisions, `tests/test_stationary.py` (new), `synthetic.py` (only additive: a still-belt scene with parts appearing/disappearing and a hand-sized blob), README + docs/design.md (short section). Contracts: prefer no change; if one is unavoidable, add fields with defaults only and justify it in the report.

## Requirements
- `belt.mode = "stationary"` (or `sorter run --stationary`): v = 0 everywhere; moving mode is untouched.
- Validity: an observation is valid when the part is fully in view, not merged, and its centre has moved < `stationary.move_tol_in` and its area < `track.max_area_change` relative to the previous observation (no belt-speed test).
- Hand / arm: a blob touching the image edge whose area exceeds `stationary.max_part_area_in2` (pick a default well above the largest in-scope part, ~1 in^2, and justify it) is ignored entirely: never a track, never classified. While a hand blob is present, tracks it covers are not finished (occlusion), and NO decisions are emitted (scene not stable).
- Settling and deciding: a part is "measured" once it has `max_feature_frames` (or at least `classify.min_frames` after `stationary.settle_s`) valid feature frames; it is then fused + classified once and keeps its id while it stays put. Parts are grouped with the SAME Grouper gap rule on their (static) positions. When every visible part is measured and no hand is present, decide each group whose membership/classification changed since its last decision and log a group record (`rule` from the existing table) plus a part record per new part. A part that is moved re-measures (new track); a part removed for `stationary.missed_s` finishes and its group is re-decided.
- No timed sends in stationary mode: the screen adapter prints/overlays the decision immediately; the decision log records the group decision; send records are either omitted or written with an explicit `stationary` marker — document which, and make `sorter grade` and `sorter report` work on a stationary session (they read part records + crops).
- Flicker is present on the rig (PWM, ~50% dimmed frames). Do not add flicker handling to the pipeline (standing decision), but the stationary decision must not flap: a part's classification is fixed once measured; re-classification only on move/removal.
- Live overlay (`sorter run --source camera --stationary` with the GUI) shows outline, id, class/verdict and the group output for each part.

## Tests (no camera)
Synthetic still scenes: (1) six classes placed one at a time with pauses -> each decided once, correct class; (2) two different parts placed touching -> one group REWORK; (3) a part removed -> its group re-decided, no duplicate decisions for untouched parts; (4) a hand-sized edge blob passing over parts -> no decisions during, no spurious tracks/parts; (5) moving mode: all existing tests (incl. goldens, determinism) unchanged. Deterministic: same stationary recording -> same decision log.

## Verification (paste each command, exit code, last ~50 lines into your final report)
`uv run pytest; echo "exit=$?"`, `uvx ruff@0.17.5 check src tests; echo "exit=$?"`, `uvx ruff@0.17.5 format --check src tests; echo "exit=$?"`, `uv run sorter run --source synthetic --headless --fast --stationary; echo "exit=$?"` (or the stationary synthetic equivalent you add).
NOTE: 5 tests in tests/replay/test_golden.py fail on this Mac (cross-machine float drift; task 021 fixes them). Those 5 are expected; nothing else may fail. Do not regenerate goldens.

## Verification Output
(orchestrator pastes the worker's report)

## Review Notes
(reviewer only)
