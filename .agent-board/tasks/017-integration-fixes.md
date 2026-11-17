# 017 integration fixes: bolt segmentation stability + seam sliver

Status: IN_PROGRESS
Dependencies: 011 (surfaced by its end-to-end tests)
Branch: `task/017-integration-fixes` (from `develop`)

## Why
Task 011's end-to-end tests exposed three downstream bugs (in 004 segment / 005 seam / 006 track) that hold the 30-part acceptance at 83% and cause spurious seam outputs. 004 and 005 passed their unit reviews because those used controlled scenes / a stand-in segmenter; the full pipeline exposes them. They are committed as 3 strict xfails in `tests/test_pipeline.py` (acceptance asserts intact, not loosened).

## Scope (files the task may touch)
src/hwsorter/vision/segment.py, src/hwsorter/vision/seam.py, src/hwsorter/vision/track.py, tests/test_pipeline.py (remove the 3 xfail marks once green), tests/test_segment.py + tests/test_seam.py (targeted regression tests). Do NOT edit contracts.py or config.py except changing default VALUES of existing keys with a measurement that justifies it.

## The three bugs (from the 011 report, with evidence)
1. **Bolt head-facet area instability.** `bolt_0375` area swings 49k–70k px frame-to-frame (true ~66.4k) even at stride 1 / coarse_scale 1 or 2. The head facet is nearly belt-bright (~178 vs belt ~185) plus its shadow; `head_width_in` reads 0.37–0.47 vs true 0.65. Result: bolts go OUT_OF_SCOPE/UNRESOLVED and the tracker's `max_area_change` invalidates observations. (steady lighting assumed — do not add flicker logic.)
2. **Bolt shank-tip fragment + duplicate tracks.** A small fragment splits off the shank tip at the right edge → a spurious track flagged MERGED+TOUCHES_EDGE; the real bolt is then flagged MERGED → UNRESOLVED → group REWORK. Bolts sometimes produce two duplicate tracks with identical extents, both MERGED, 0 valid frames.
3. **Seam ROI-top sliver + ON_SEAM over-flagging.** segment() returns a sliver along the ROI top where the seam's top end meets the belt edge (bbox ~(247,56,872,115), area ~0.022 in², just over the 0.15 in edge-strip rule); seam.py flags it on_seam but does NOT remove it → spurious tracks/parts/MISC on empty seam laps. Also seam.py over-flags ON_SEAM (a part clipped at the right image edge looks like a seam dip).

## Acceptance (remove the xfail marks; all must pass, nothing else regressed)
- `test_end_to_end_30_parts_with_seam`: ≥95% correct, zero bin contamination, one track/decision/send per part.
- `test_seam_alone_never_produces_output`: empty seam laps emit no part/group/send.
- `test_bolt_on_seam_is_one_part_not_split`: a bolt on the seam is one track.
- All existing segment/seam/classify/track/pipeline tests still pass; determinism holds; coarse_scale 1-vs-2 equivalence still holds; `uvx ruff` clean.

## Verification Output
(worker pastes each command, exit code, last ~50 lines)

## Review Notes
(reviewer only)
