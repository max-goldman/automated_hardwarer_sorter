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

## Session 4 stop (2026-11-17, user out of usage): WIP 56af8c8, NOT verified or reviewed
Worker's final status:
- **Done:**
  - `segment._trim_soft` seeds the peel only from soft pixels on the window border, i.e. the open belt. A soft region sealed off by the silhouette edge and next to exactly one mask component (no bigger than it) is added to the mask (`_enclosed_faces`). Pinholes are sealed with `_TRIM_BRIDGE_PX=5`.
  - bolt.py measures along the shank axis (`_oriented_profile`: the minAreaRect axis, corrected twice by the shank-edge slope). An ideal T-polygon gives identical values at every angle 0-165°.
  - `hole_dark_threshold` changed from 0.30 to 0.45 (config.py + default.toml). The goldens embed the config, so they WILL change and must be regenerated with justification.
- **Verified:** test_robustness, test_segment* and test_features* passed at 65e0b95, before the threshold change. NOT run: the full suite, goldens, bench, still-* replays.
- **Per-frame diagnostic** (tools in `.agent-board/tools/028/`; before table in before.txt):
  - bolt: CONFIDENT 40→129, OOS 263→100, UNRESOLVED 18→92.
  - nut: hole_kind OOS 87→0, CONFIDENT 155→173, UNRESOLVED 135 (circularity).
  - washer: 299/299, unchanged.
  - No wrong-class CONFIDENT anywhere. Bolt head_width max 0.514 (the 5/16 range starts at 0.52: thin margin, watch it).
- **head_width vs angle** (real data covers only two bins): 15-30° 0.325→0.335 (n=75); 30-45° 0.400→0.507 (n=246).
  - Remaining bolt OOS: motion blur (4-18 px) loses the lower head facet, leaving a ~13 px gap at the head/shank corner. Bridge 15 helped the bolt (CONF 159) but made the nut rounder, so it stays at 5.
- **Bolt UNRESOLVED:** head_length reads 0.19-0.21 against an upper bound of 0.195. That is parallax rising with x (0.172 at x≈600, 0.209 at x≈1050), so task 027, not the angle.
- **Moving-nut root cause:** in 0-25nut1 the nyloc insert faces up. Its bore reads 0.21-0.35 of the belt level, brightest near the image centre. The stills were thread side up and read 0.09-0.14. Motion blur is minor.
  - Circularity reads 0.957 static and ~0.965 moving, against an upper bound of 0.965. A range change to ~0.972 is out of this task's scope and needs the library safety test (nut vs washer [0.975,…]).
- **To do (fresh worker):**
  1. Full pytest; fix failures.
  2. Regenerate the goldens (scripts/update_goldens.py, local.toml absent) and justify the change.
  3. ruff; `sorter bench synthetic --parts 30`; the six still-* replays.
  4. Synthetic tests: a belt-toned enclosed head facet, head_width at 0-180°, exactly one blob in test_shadow_trim_never_splits_a_blob.
  5. Illumination clamp 0.7-1.3 with limited reuse of the last surface.
  6. docs/design.md note.
  Then a fresh Opus review.
