# 011 pipeline runner, decision log, sorter run

Status: TODO
Dependencies: 002b, 004, 005, 006, 007, 008, 009, 010
Branch: `task/011-pipeline` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/pipeline.py (new), src/hwsorter/cli.py (add `run` subcommand and wire `replay` through the pipeline), src/hwsorter/session.py (only to plug the pipeline in as a capture consumer), src/hwsorter/vision/track.py (ONLY to add a read-only public `t0_ns` property), tests/test_pipeline.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it or config.py; request changes via the task file).
Implement `Pipeline` per contracts-spec.md: Preprocessor -> segment -> SeamHandler -> Tracker -> features.extract per valid observation -> fuse on finish -> classifier -> Decider -> send_time_ns -> Scheduler.submit. Applies camera latency exactly once (invariant 2). Writes the decision log (kinds part/group/send/session_start/session_end per the contracts schema; stage timings under a separate `timings` key). Saves per-part crops + full frame reference under the session folder for grading (config pipeline.crop_dir). Exposes a `PipelineSnapshot` for the GUI (live tracks with outlines, ids, predicted output, confidence, gaps, rework flags, pending sends with countdown, fps, drops, latency).
CLI: `sorter run [--source camera|synthetic|<recording>] [--headless] [--realtime|--fast]` and make `sorter replay <recording> --pipeline --decision-log <path>` run the identical pipeline with MockSerialAdapter + ManualClock (or rebased times).
Tests: end-to-end on a synthetic run of the 30-part set (all six classes, five each, singulated at spacing > rework gap): every part gets exactly one ID, one decision, one send; >= 95% correct output; zero contamination; the seam never produces an output; two touching parts give REWORK or a same-class bin per the table. Deterministic: two replays of the same synthetic recording give identical decision logs excluding `timings`. Report per-stage mean/p99 ms per frame and name the CPU. No camera is available: use `--source synthetic` or generated recordings only.

- From 006 review: Tracker needs a public `t0_ns` so the pipeline can compute `x_lead_in_at_ref`; add it as a read-only property.
- From 009: `Grouper.advance` checks only entry belt_x. The pipeline must call `advance` only when no live track lies in the span behind a group (invariant 6), or pass a bound that accounts for live tracks.
- Processing budget (ADOPTED, `<REPO_ROOT>/.agent-board/budget-proposal.md`): process only frames with `seq % pipeline.frame_stride == 0` (skipped frames are dequeued and dropped deterministically); extract features on valid observations where `(seq // frame_stride) % feature_stride == 0`, at most `max_feature_frames` per track, as frames arrive; replay blocks rather than dropping; any queue overflow is logged and counted as a FAIL.
- Lighting: assume steady (see board).
- Optional from 009 review: the pipeline may compute the frontier `max(entry_belt_x, max live-track lead)` itself before calling `Grouper.advance`; decide/ is merged, so don't change its signature.
- Optional from 006: flag a sudden area drop or split as MERGED to catch parts that enter the view already fused.

## Verification Output
(worker pastes each command, exit code, last ~50 lines)

## Review Notes
(reviewer only)
