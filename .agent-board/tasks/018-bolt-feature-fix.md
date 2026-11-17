# 018 bolt feature fix: head_length_in robustness + axis-aligned bolt

Status: DONE (merged into develop, session 3). head_length_in now measured from the head plateau (not below[0]); the previously-missed bolt (256.8 deg) reads 0.264 and classifies -> 30-part is 30/30, zero contamination, 282 passed. No parts_library change (bolt_0375 head_length 0.249-0.264 vs range [0.17,0.29]). Axis-aligned bolts already handled by the minAreaRect w<h swap. The bolt-on-seam xfail is NOT a feature bug — it is seam._repair fusing the bolt with the stitch strip; accepted as safe REWORK (user decision) and parked on hardware-pending.
Dependencies: 017 (merged), 007 (the features module), 011 (the e2e test)
Branch: `task/018-bolt-feature-fix` (from `develop`)

## Why
After 017, the 30-part run is 29/30 and `test_bolt_on_seam_is_one_part_not_split` is the last xfail. Both trace to ONE feature-extraction bug in `src/hwsorter/vision/features/bolt.py::_bolt`: the width-profile head-end taper makes `head_length_in` read ~0 on a tapered or axis-aligned bolt head (the "head end first" flip leaves the outermost head slice tapering to 0, so `below[0] = 0`). A bolt whose head_length reads 0 → OUT_OF_SCOPE → MISC (a safe miss, but a miss). The single missed part in the 30-part run is such a bolt; the bolt-on-seam case is an axis-aligned (angle 0) bolt whose width profile is additionally near-degenerate.

## Scope (files the task may touch)
src/hwsorter/vision/features/bolt.py, tests/test_features.py (regression), tests/test_pipeline.py (remove the LAST xfail once it passes). config/parts_library.yaml ONLY if a head_length/length range must be adjusted for the now-correct feature — justify it with the measured values and report it. Do NOT edit contracts.py, config.py, pipeline.py, other feature modules, or segment/seam/track.

## The fix (from 017's handover + review)
1. `head_length_in`: measure the head length from the head PLATEAU (e.g. the last index whose width is above the shank/head midpoint, or a width-threshold crossing), NOT `below[0]` (the first sample under the midpoint), which collapses to 0 on a tapered head tip after the head-end-first flip.
2. Axis-aligned bolt (angle 0/180): the minAreaRect long axis is ambiguous for an axis-aligned shape — ensure the bolt's major (shank) axis is identified correctly so the width profile runs along the shank, not across it. Keep it rotation-invariant (bolts at all 8 synthetic angles must still read correctly).

## Acceptance (remove the last xfail; nothing regressed)
- `test_bolt_on_seam_is_one_part_not_split` PASSES (xfail mark removed, asserts intact).
- `test_end_to_end_30_parts_with_seam` reaches 30/30 = 100% (still zero contamination, one track/decision/send per part).
- All existing test_features / test_classify / test_segment / test_seam / test_pipeline / test_core tests pass; determinism + coarse_scale equivalence hold; `uvx ruff` clean.
- No new wrong-class contamination.

## Verification Output
(worker)

## Review Notes
(reviewer)
