# 028 bolt head at any angle, shadow-trim facet protection, moving-nut robustness (+ 023 review follow-ups)

Status: IN_PROGRESS
Dependencies: 023, 024 merged. Parallel with 029 (tracker/pipeline/gui) — disjoint files.
Branch: `task/028-segment-followups` (from `develop`)
Worker: Opus. Reviewer: fresh Opus.

## Evidence
New user recordings (MAIN checkout `<REPO_ROOT>/recordings/`, 8 ms exposure, belt ~238, lights brighter than the flat-field's 202): `*-0-25bolt1` (12.3 s), `*-0-25nut1` (7.2 s), `*-0-25washer1` (7.2 s). One 1/4 part slid/pushed around the view (moving, at angles, through edges). Per-frame single-frame classification of every non-edge blob (diagnostic script: `/private/tmp/claude-501/-Users-maxalex357-Documents-automated-hardwarer-sorter/052daeb7-6b3a-4d5e-9709-d3bbee9d57cd/scratchpad/perframe.py <names>`; it uses measure._rig_from_calibration, features.min_valid_frames=1 and classify.min_frames=1):
- washer: 299/299 CONFIDENT washer_025.
- nut: 155 CONFIDENT nut_025, 87 OUT_OF_SCOPE (worst hole_kind), 66 UNRESOLVED (worst circularity).
- bolt: 40 CONFIDENT bolt_025, 263 OUT_OF_SCOPE (worst head_width_in; median head_width 0.40 vs true 0.4315 AF / 0.498 corners, range [0.45,0.54]), 18 UNRESOLVED. The bolt lies ~20-35° from the travel axis in most frames. On axis-aligned still recordings head_width reads 0.45-0.51.
- No wrong-class CONFIDENT in any frame (keep it that way).
023 Opus review finding: `_trim_soft` peels the belt-toned upper head facet of the bolt (5/16 bolt head_width 0.561 MAD 0.025 with trim, 0.586 MAD 0.0003 without).

## Scope
src/hwsorter/vision/segment.py (trim / illumination), vision/features/bolt.py (+ geometry/holes as needed), config keys in segment/features sections, synthetic.py (additive: bolt-like head facets at belt tone, arbitrary angles, motion blur option), tests. Not the tracker, pipeline, stationary logic, gui (task 029 owns those), and not library ranges unless a feature's definition changes (then re-validate with tests/test_library_safety.py and the real recordings).

## Requirements
1. Bolt head width and head length invariant to the bolt's angle (0-180°): measure along and across the bolt's own axis (PCA/minAreaRect of the shank), not image axes. Report head_width vs angle on the real recordings (bin by 15°).
2. Shadow trim must not peel a low-contrast part face that is enclosed by the part's sharp edges (protect pixels inside the convex hull of the sharp-edge mask, or equivalent), while still removing attached soft shadows (keep 023's shadow tests green).
3. Moving nut: find why hole_kind and circularity fail in ~half the frames of `0-25nut1` (motion blur at 8 ms? the nyloc insert? edge proximity?) and fix what is fixable in segmentation/features. If it's motion blur from the operator pushing parts, say so and quantify it (blur px vs failure).
4. 023 optional follow-ups: clamp the illumination surface (e.g. 0.7-1.3) and limit reuse of the last surface; tighten test_shadow_trim_never_splits_a_blob to assert exactly one blob.
5. Keep: all six still-* real recordings CONFIDENT and correct (stationary replay `sorter run --source still-<n> --stationary --headless --fast`), synthetic acceptance 30/30, library safety test green, goldens (regenerate only if features legitimately change; justify).

## Verification
pytest fully green; ruff check + format --check; `sorter bench synthetic --parts 30`; the per-frame diagnostic on the three new recordings before/after (paste the tables); the six still-* replays; head_width-vs-angle table.

## Verification Output
(orchestrator pastes the worker's report)

## Review Notes
(reviewer only)
