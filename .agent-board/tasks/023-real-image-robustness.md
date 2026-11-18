# 023 real-image robustness: illumination, shadows, outline-based features, seam false positive

Status: DONE (merged)
Dependencies: 019, 020 merged
Branch: `task/023-real-image-robustness` (from `develop`)
Worker: Opus (vision, real data). Reviewer: fresh Opus.

## Evidence (first real images, rig Mac, stationary belt, 8 ms exposure, scale 387.7 px/in)
Recordings (raw, untracked, in the MAIN checkout `<REPO_ROOT>/recordings/`): `*-still-bolt025`, `*-still-bolt0375`, `*-still-nut025`, `*-still-nut0375`, `*-still-washer025`, `*-still-washer0375` (one part each, centred, 2 s), plus empty belts `*-empty-belt-still` (used for the current flat-field) and `*-empty-belt-still2` (later, darker). Measure any of them with `uv run sorter measure still-<name> --seconds 10 --out <dir>` (writes overlay.png/mask.png/measure.json). Calibration + flat-field: `<REPO_ROOT>/config/calibration/bananabook-pro-m5{.json,.flatfield.npy}`. Run from the MAIN checkout root via `HWSORTER_ROOT=<REPO_ROOT>` (or copy the calibration into your worktree; never commit calibration or recordings).
Current results (all six should be CONFIDENT and correct; none are):
- Illumination is UNEVEN and DRIFTS: relative to the flat-field, 4x4 cell medians vary 0.90-1.06 within one frame, and the pattern changes between captures (room light, operator position). `segment.deviation_threshold` is 0.12, so margins are ~5%.
- Soft shadows attached to parts are included in outlines (bolt0375: head shadow -> head_length 0.315 > 0.29, total length 1.39; nut0375: ragged square of shadow around the hex -> circularity 0.57).
- circularity on real outlines: nut025 0.66 (clean hex outline, ideal ~0.91), washer0375 0.85 with MAD 0.14 between near-identical frames (outline jitter). Smooth synthetic outlines hid this.
- bolt025 shank_width 0.316 in (true major 0.25) with MAD 0.067 on a static scene: unstable on real threads; bolt0375 shank 0.307 (≈ root diameter).
- washer025 hole read as hole_kind 2 (dark) though the belt is visible through it: the belt there reads ~0.89 vs `features.hole_bright_threshold` 0.90.
- SeamHandler reports a seam (drawn in the top-right) on frames with no seam: the dark strip above the belt's curved top edge.
- Diameters inconsistent: washer025 OD 0.653 (nominal 0.625), washer0375 0.772 (nominal 0.8125). Caliper truths are being requested from the user; do NOT retune library ranges in this task.

## Scope
src/hwsorter/vision/segment.py (Preprocessor + segment), vision/seam.py (false-positive only), vision/features/ (geometry circularity, bolt shank, holes), config.py + config/default.toml (new keys with defaults allowed in the segment/features/seam sections), synthetic.py (additive: illumination gradient, attached soft shadows, outline noise options), tests. Do not touch the tracker, decider, stationary logic or contracts.py.

## Requirements
1. Per-frame low-frequency illumination correction after the flat-field: estimate a smooth surface (e.g. a low-order polynomial or heavily blurred median) from belt pixels only (exclude the previous/coarse part mask and outside-ROI), divide it out, so a ±10% smooth gradient leaves belt at 1.00±0.02. Must be cheap (coarse grid; budget: total preprocess+segment stays ≤ ~3 ms/frame on the M5 at frame_stride 2 — measure with `sorter bench`).
2. Attached soft-shadow trimming: shadows are shallow and have soft edges; the outline must follow the part's sharp edge, not the shadow. Use the existing edge-gradient evidence; justify the method. Show before/after overlays for bolt0375 and nut0375.
3. Circularity robust to pixel-level outline noise (e.g. perimeter of a lightly smoothed / resampled contour or of the convex hull for convex parts); hex nut ≈ 0.90-0.92, round washer ≈ 0.98-1.0 on real outlines, low MAD.
4. Bolt shank width stable on real threads (median over the shank region, robust to thread crests/roots and highlights); report what diameter it measures (major/minor/mean) and make synthetic consistent with it.
5. Hole kind judged relative to the local belt level around the part (ring outside the outline), not the absolute 0.90.
6. Seam detector must not fire on the curved dark top edge / outside-ROI strip on these recordings (seam detected: False on all six), while existing seam tests still pass.
7. Moving-mode determinism tests and goldens: if changes legitimately alter golden feature values, regenerate goldens with `scripts/update_goldens.py` and say exactly why; acceptance (30/30, zero contamination) must still pass.

## Verification
pytest fully green; ruff check + format --check exit 0; `sorter bench synthetic --parts 30` timing; and for EACH of the six real recordings: the `sorter measure` part table + per-feature block (before and after), and the overlay PNG paths. Report which parts are now CONFIDENT/correct and which remaining misses are range issues (to be fixed with caliper truths).

## Verification Output
Worker (Opus), f5b703b: pytest exit=0; ruff/format exit=0; bench PASS (per captured frame mean 4.00 -> 3.65 ms, p99 18.8 -> 16.6). Real: bolt025/nut025/washer025 CONFIDENT correct (were UNRESOLVED/OOS/OOS); the 5/16 three are UNRESOLVED against the old 3/8 library (range issue, task 024). Washer OD 0.6246/0.7517 vs calipers 0.6244/0.7508 (scale k=1.000, edge bias +-0.2 px). Thick parts +3-5% = height parallax (implied camera height ~8.2 in). Seam: 0/200 false hits on all; seam ghost fixed (min_piece_in2). Goldens regenerated (verdicts unchanged). test_six_classes_one_at_a_time: parts removed 0.6 s, because of the tracker IoU weakness (nut->washer swap IoU 0.90).

## Review Notes
Round 1 (fresh Opus): APPROVE. Merged tree: pytest exit=0, ruff/format exit=0, 30-part run 30/30/30 0 late; bench 3.19 ms per captured frame. Reviewer's measure on real: bolt025 CONF 0.30, nut025 CONF 0.70, washer025 CONF 0.21; the 5/16 three UNRESOLVED by range only. Required follow-ups (non-blocking): (1) _trim_soft peels the belt-toned upper head facet of the 5/16 bolt (head_width 0.561 MAD 0.025 vs 0.586 with trim off): protect low-contrast faces inside the hull of the sharp-edge mask; (2) the tracker reappear check (026), incl. the hand-in-view case. Optional: clamp the illumination surface (0.7-1.3), limit reuse of the last surface, tighten test_shadow_trim_never_splits_a_blob. Notes for 024: circularity nut ~[0.88,0.96], washer ~[0.975,1.02]; thick-part ranges at apparent size (parallax +2.5-4.5%); don't separate bolt sizes on head_width/minrect_short.
