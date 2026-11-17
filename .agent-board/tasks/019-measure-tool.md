# 019 `sorter measure`: per-part measurement diagnostic on real frames

Status: DONE (merged)
Dependencies: none (M2/M3 merged)
Branch: `task/019-measure-tool` (from `develop`)
Priority: HIGH — the user is at the rig with a STATIONARY belt now and wants same-day results.

## Why
The parts library ranges come from synthetic nominal parts. We need to see, on real images, (a) whether segmentation outlines real parts correctly and (b) how each real part's fused features compare with the library ranges. Full `sorter run` cannot do this on a stopped belt (task 020), so this is a standalone diagnostic that does not use the tracker, grouper or scheduler.

## Scope (files the task may touch)
`src/hwsorter/measure.py` (new), `src/hwsorter/cli.py` (registration hook + thin `cmd_measure` call only), `tests/test_measure.py` (new), README (one row in the M2/M3 command table + one example line). Do NOT edit contracts.py, config.py, vision/*, classify/*, camera/*.

## Requirements
- `sorter measure <source> [--seconds S] [--max-frames N] [--out DIR] [--include-dimmed]` where source is `camera`, `synthetic`, or a recording name/path (reuse the same source resolution as `record`/`run`).
- Per frame: the same preprocess + `segment(prepared, cfg.segment, mapper)` + seam handling + `features.extract` as the pipeline uses (call the existing functions; do not reimplement). Use the real `PlaneMapper` from the machine calibration; if scale is uncalibrated, print a clear WARNING that inches are provisional.
- Stationary assumption: cluster blobs across frames by centroid in inches (within `track.gate_in`) into "parts" with stable ids numbered left to right. Blobs touching the image edge are reported but marked EDGE.
- Lighting flicker is present on the rig (PWM dimming, ~50% of frames dimmed). By default SKIP dimmed frames: compute the per-frame belt brightness the way `capture.py`/`calibration/exposure.py` already does (75th percentile of the ROI centre), and drop frames below 0.9 x the session's 90th percentile of that value. Report how many frames were used/skipped. `--include-dimmed` disables this.
- Per part: median and MAD of every feature over the frames used (use `vision.fusion.fuse` if it fits, else the same median/MAD), then `RangeClassifier` with the loaded `config/parts_library.yaml` and `cfg.classify`.
- Output, printed and saved under `--out` (default `sessions/measure-<timestamp>/`):
  1. A table per part: id, centre (in), n frames, verdict, best class + pose, score, runner-up class + score, worst feature.
  2. For each part, every feature of its best class: median, MAD, the library range [lo, hi], and an OK/LOW/HIGH mark, plus how far outside (in inches or units).
  3. `measure.json` with all of the above (machine-readable; the orchestrator will read it).
  4. `overlay.png`: one representative (undimmed) full frame with each part's sub-pixel contour drawn, its id and best class + verdict written next to it, and the seam band if detected. Also `mask.png` (the segmentation mask) for the same frame.
- Live camera: run for `--seconds` (default 3), then stop and report. Must not modify camera code; use the existing Session/capture path.
- Pipeline core rule still applies: no Qt imports in `measure.py`.

## Tests (no camera)
On a synthetic still scene (parts placed, belt speed effectively 0 — use `synthetic.py`; if it cannot render a still belt, render a single frame repeatedly): every part found once, ids left-to-right, correct best class for each of the six classes, `measure.json` and both PNGs written, dimmed-frame skipping works on a scene with synthetic flicker (synthetic.py has an optional flicker), and the CLI exits 0.

## Verification (paste each command, exit code, last ~50 lines into your final report)
`uv run pytest; echo "exit=$?"`, `uvx ruff@0.17.5 check src tests; echo "exit=$?"`, `uvx ruff@0.17.5 format --check src tests; echo "exit=$?"`, `uv run sorter measure synthetic --out /tmp/<scratch>; echo "exit=$?"`.
NOTE: 5 tests in tests/replay/test_golden.py fail on this Mac (cross-machine float drift; task 021 fixes them). Those 5 failing is expected; anything else failing is not.

## Verification Output
Worker (Sonnet), commit 15b90d2: pytest exit=1 with only the 5 known golden float-drift failures (fixed by 021); test_measure.py passes; ruff check exit=0; format exit=0; `sorter measure synthetic` exit=0, six parts CONFIDENT with correct classes, all features OK.
Orchestrator live check on the rig camera (empty belt, 8 ms exposure, uncalibrated): exit=0, 200 frames used, 0 dimmed, no parts, 1 sporadic blob ignored. A false seam was drawn at the top-right where the dark strip meets the belt (no ROI/flat-field yet): re-check after calibration.

## Review Notes
Round 1 (fresh Sonnet reviewer): APPROVE. pytest only the 5 known failures; ruff/format exit=0; pipeline-call fidelity confirmed. OPTIONAL: `session.start()` outside the try in `_grab_camera`; sporadic clusters are only counted, not listed with positions.
