# 006 tracking

Status: DONE
Dependencies: 002
Branch: `task/006-track` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/vision/track.py, tests/test_track.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it unless this IS task 002).
Implement `Tracker` per contracts-spec.md. Parts are stationary in belt coordinates; gated nearest-match on predicted position (track.gate_in). Observation.valid only when fully in view (not touches_edge), moving at belt speed within tolerance, stable outline (area change small vs previous). Flags: MERGED when two tracks' predictions map to one blob, UNSETTLED when never valid for settle_frames, TOUCHES_EDGE, FEW_FRAMES. A track finishes when it leaves the view (or after max_missed_frames). `entry_belt_x_in(t)` = belt_x of the upstream (left) image edge at time t. Tests with fakes and synthetic moving scenes: one ID per part through the view, three parts in view at once keep separate IDs, merge flagged, belt_x constant within 0.01 in.

## Verification Output
Commit: `11b87be` on `task/006-track`. Worktree: `<REPO_ROOT>/.claude/worktrees/agent-a01e83b40e3ad48a2`

- `uv sync`: exit 0
- `uv run pytest -q`: exit 0 (all pass, 58 tests incl. 8 in tests/test_track.py); tail: `..........................................................  [100%]`
- `uvx ruff@0.17.5 check src tests`: exit 0; `All checks passed!`
- `uvx ruff@0.17.5 format --check src tests`: exit 0; `43 files already formatted`
- `uv run sorter --help`: exit 0; prints subcommands (gui, record, replay, recordings, calibrate) and options.

Design choices:
- t0 = t_capture_ns of the first update()/entry_belt_x_in() call; image width comes from `mapper.scale.image_width`, left/right edges = px_to_in of (0|W, H/2).
- Gating: belt-frame x distance = min(centre, lead edge, trail edge) diffs (part clipped on one side while entering/leaving), plus centre y; hypot <= gate_in; greedy ascending assignment. Prediction = stationary in belt frame (last position).
- Valid = not touches_edge AND not merged AND speed ok (belt-frame drift vs earliest non-edge obs within last settle_frames <= speed_tolerance*v*dt; skipped when no reference) AND area change vs previous full-view obs <= max_area_change.
- MERGED: a blob that contains the predicted centres of >= 2 live tracks becomes an invalid observation of each, both flagged MERGED; ids persist and re-link by gate after separation. Two parts that enter already fused form one track (undetectable).
- Finish: predicted trailing edge past right image edge, or > max_missed_frames consecutive misses. At finish: UNSETTLED = no run of settle_frames consecutive valid obs; FEW_FRAMES = valid obs < min_frames; TOUCHES_EDGE = never seen fully inside the image; ON_SEAM copied from Blob.on_seam.
- Extents (belt_x lead/trail, y) = median over valid observations (all if none valid). No config default values changed.
- Synthetic test uses a local absdiff-vs-empty-scene threshold segmenter; its shadow edge flickers under illumination falloff, so the synthetic belt_x check allows 15% frames >0.01 in (max 0.03); strict < 0.01 in range is asserted on noise-free fake blobs.

## Review Notes
Reviewer re-ran in worktree (authoritative): `uv run pytest` exit=0 (58 passed); `uvx ruff@0.17.5 check src tests` exit=0; `format --check` exit=0 (43 files formatted); `uv run sorter --help` exit=0. Diff touches only vision/track.py and tests/test_track.py (in scope).

APPROVE

Findings (all verified, none blocking):
- entry_belt_x_in (track.py:82) is correct: left edge x_in via px_to_in(0, H/2), minus v*(t-t0). Invariants 1-4 hold: px_to_in only (no px_per_in math), lead = max x, trail = min x, belt-frame prediction is stationary.
- Finish (track.py:141-152) is correct: a track that vanished at the right edge ends as soon as its last trail + shift passes the right edge, and a mid-view dropout ends after max_missed_frames+1 misses. Matched tracks never finish early. Iteration order is by track_id, so output is deterministic.
- Belt_x computation is NOT buggy. I measured the synthetic test per part: bolt and washer deviate at most 0.0013 in, with no drift trend. Only the nut track has outliers (max 0.021 in, 9% of frames over 0.01), from the test-local threshold segmenter's shadow edge (lead and trail sd 0.02 vs 0.0008 for the others). The relaxed tolerance is a test-helper artefact, and the strict 0.01 bound is asserted on fake blobs.
- Valid (track.py:177-196) matches the spec: not touches_edge, not merged, belt-frame drift vs the earliest non-edge obs in the last settle_frames within speed_tolerance*v*dt, and area change vs the previous full-view obs within max_area_change.
- Extents (track.py:206-221): lead, trail, y_min and y_max are medians over valid observations only, with a fallback to all observations when none are valid. That is right for grouping, because clipped and merged blobs would inflate the extents. t_first_ns and t_last_ns use all observations (the track lifetime), which suits group closure ("no live track in span"). Accepted.
- Determinism: sets are used only for membership, pairs are sorted on (dist, tid, j), states are sorted by id, and there are no time.* calls.
- MERGED (track.py:102-110) is sound for the case it handles: a point-in-polygon test of each predicted centre against each blob, with ids kept and re-linking by gate after separation (covered by test_merge_flagged_then_separate). The blob is not used to update either track's state, which is correct.

OPTIONAL (non-blocking):
1. track.py:82-86, 77: t0 is "first update/entry_belt_x_in call", not an injected session start, and no accessor is public. The pipeline (task 011) needs t0 for x_lead_in_at_ref = belt_x + v*(t_ref - t0). A one-line `t0_ns` property would remove the hidden coupling. If skipped, the pipeline must call entry_belt_x_in(session_start) first, and that has to be documented.
2. Entering-fused parts (one blob from first sight) form a single track and are not flagged. A cheap mitigation that fits the contract is a split check: when a fully-in-view, non-merged track's area drops by more than about 40% at one step and a new track spawns within gate_in in the same frame, flag the old track MERGED. This is not required now. Without it, the fused blob's size should drive OUT_OF_SCOPE/UNRESOLVED downstream. Revisit after the classifier (task 008) exists.
3. track.py:173-175: a merge is detected only if both predicted centres fall inside the blob. A fused blob that covers only one centre (concave neck, or a bore) is just greedy-matched. This is acceptable.
4. track.py:206-221: _refresh_extents rescans all observations every frame, so a track costs O(n^2) over its life. It is harmless at ~150 frames. An incremental update would be cleaner.
5. track.py:170: the gate takes the min of centre, lead and trail x distance, which is permissive for adjacent parts at similar y. The y term and gate_in=0.5 bound it. Fine for now.
