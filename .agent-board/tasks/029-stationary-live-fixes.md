# 029 stationary live fixes: static-track churn, partial hands, live throughput (+ 026 reappear check)

Status: IN_PROGRESS
Dependencies: 020, 023, 024, 025 merged
Branch: `task/029-stationary-live-fixes` (from `develop`)
Worker: Opus. Reviewer: fresh Opus. Absorbs task 026.

## Evidence: the user's first live trial (recording `rig1`, 12.7 s, `<REPO_ROOT>/recordings/20261117-203044-rig1`, session `<REPO_ROOT>/sessions/20261117-203043`)
A 1/4 bolt lies still in view the whole time; from about 4 s, fingers reach in from the right image edge (never covering the bolt). User: "does not detect till my hand comes in, then it detects both my hand and the bolt. but it won't detect the bolt on its own."
- Segmentation is fine: `sorter measure rig1` sees the bolt in 360/360 frames, CONFIDENT bolt_025 (score 0.98, head_width 0.4508 at the 0.45 lower edge).
- Replay `sorter run --source rig1 --stationary --headless --fast`: bolt decided bolt_025 at ~0.5 s, then track #1 ENDS by 1.0 s although nothing moved; new tracks #2, #3, #16 ... re-measure the same static bolt, several go misc (head_width just under 0.45). Status alternates "in view: 1 parts" / "0 parts".
- Fingers (touching the right image edge, < 1 in² until they reach further) are never a hand ("hand: no" throughout), so they become tracks and get decided misc.
- Live with the window + `--record`: 656 `queue_overflow` events; the pipeline processed 761 of ~1530 frames.
- Belt level is now 237 (lights turned up) vs the 202 flat-field.

## Scope
`src/hwsorter/vision/track.py` (stationary logic), `src/hwsorter/pipeline.py` (stationary hand/edge rules, throughput), `src/hwsorter/gui/run_window.py`, `cli.py`/`session.py` (only for throughput), `config.py` + `config/default.toml` (`[stationary]` keys), tests, docs/design.md (stationary section). Not vision/segment, features, classify ranges, or contracts.py (unless unavoidable: defaulted fields only).

## Requirements
1. **Static-track churn:** find why a locked, unmoving track finishes (relocate? the IoU or area-off check against the anchor? missed frames from stride/hold? the anchor taken from a different-quality frame?). Fix so a part that does not move keeps ONE track and ONE decision for the whole session. On rig1 replay: the bolt is decided exactly once, bolt_025, and never re-measured.
2. **Partial hands / edge blobs:** in stationary mode, a blob touching the IMAGE or ROI edge must never be decided as a part. Holding decisions: hold while an edge-touching blob is present and changing (moving or growing), or above max_part_area_in2. A static part left at the edge must not block decisions forever (it is reported, never decided). On rig1: fingers never produce a part or group record, and the bolt's decision is unaffected.
3. **Reappear check (was 026):** a part swapped in place (e.g. nut→washer, silhouette IoU ~0.90) must be re-measured: on reappear after a gap or a hold, compare area (and hole kind when available) with the anchor as well as IoU. Then restore `test_six_classes_one_at_a_time` to the short removal gap that 023 lengthened to 0.6 s, and add a quick nut→washer swap test.
4. **Live throughput:** with `sorter run --source rig1 --stationary --record x` (window, realtime pacing; run offscreen with QT_QPA_PLATFORM=offscreen), zero queue_overflow on the M5. Profile first (pipeline per-frame cost vs window paint vs recorder vs the GIL). Likely suspects: the window repainting or converting every frame (cap the display at about 30 fps and convert only displayed frames), frame_stride applied after queueing, overlay work per frame. Report before/after overflow counts and per-stage ms.
5. Regression: all moving-mode tests and goldens unchanged; the existing stationary tests pass.

## Verification
pytest fully green; ruff check + format --check exit 0. rig1 replay (headless fast): the DECISION lines and the status line sample. rig1 realtime with the window offscreen + --record: the overflow count. Also the six still-* recordings, stationary headless fast: all CONFIDENT and correct (as on develop now).

## Verification Output
(orchestrator pastes the worker's report)

## Review Notes
(reviewer only)

## Session 4 stop (2026-11-17, user out of usage): WIP d32957f, NOT verified or reviewed
Worker's final status:
- **Done (worker-checked):**
  - rig1 headless fast replay: exactly one decision, `group 1 [#1] -> bolt_025 (lone_confident)`. The bolt stays "1 parts (1 measured)" throughout, and the fingers produce no part or group.
  - tests/test_stationary.py: 26 passed before the turned/shifted test was added; that test passes alone.
  - ruff was clean at the previous commit.
- **Revert proofs** (they failed as they should): the hole-kind check and the turned/shifted reappear check. The nudge-follow and finger-edge proofs were not finished.
- **Churn root cause:** the bolt really moved. It slid ~0.3 in at 1.6-2.4 s and was pushed by fingers at 4.2-5.3 s. The old rule finished a measured track once it was > relocate_in (0.15) from its measured spot, even while it was tracked every frame. There was also a bug: any other blob inside the gate marked the track displaced.
  - Fix in track.py: a part seen every frame as its own blob, with its measured area, keeps its track, and the anchor follows it.
  - After a gap, a hand hold, a merge or an edge contact, its identity is re-checked: within 0.15 in of the anchor, area within 15%, centroid-aligned IoU ≥ 0.9, and hole kind via `Pipeline._same_hole`.
  - Confirmed tracks claim blobs first.
- **Edge rule:** the edge complex is every blob touching the image or belt edge, plus blobs within `edge_link_in` (0.25 in) of one, now or in the last `after_hand_s`, chained.
  - Those blobs are never clean, measured or decided.
  - Decisions and finishing wait while the complex changes (more than 2× min_area of change) and for `after_hand_s` after. A static edge blob stops holding decisions.
- **Throughput root cause:** segment takes ~9 ms per frame, 27-32 ms with fingers in view. It is Python/GIL-bound, so 60 processed fps can't keep up: the baseline had 665 queue_overflow.
  - Fix (not measured): `stationary.frame_stride` = 6 (20 fps), `feature_stride` = 1, and the capture queue takes only on-stride frames.
  - Skipped-frame snapshots now carry the last tracks, which removes the "0 parts" flicker.
- **To do (fresh worker):**
  1. Full pytest + ruff on HEAD. test_run_visibility and the capture/session tests may be affected.
  2. rig1 realtime run with the window offscreen and `--record`: overflow count and per-stage ms.
  3. The six still-* recordings in stationary mode.
  4. Status line and overlay: show n_edge / edge_hold.
  5. docs/design.md stationary section.
  6. The nudge-follow and finger-edge revert proofs.
  Then a fresh Opus review.
