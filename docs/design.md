# Design note: the vision pipeline (milestones 2 and 3)

Requirements are in `descriptions/Hardware Sorter Vision Program Project Brief.md`. This note
says how the code meets them, how to extend it, and where it is known to fall short.

## Approach

One camera watches a belt moving at about 4 in/s. Every frame goes through a fixed chain of
stages; each stage is a module with a small, typed interface (`src/hwsorter/contracts.py`).

```
frame -> segment -> seam -> track -> features -> fuse -> classify -> decide -> schedule -> output
```

| Stage | Module | Job |
| --- | --- | --- |
| segment | `vision/segment.py` | Flat-field the frame, threshold, return one `Blob` (sub-pixel outer contour) per object. Two-stage: coarse pass on a downscaled frame, then crops. |
| seam | `vision/seam.py` | Find the diagonal belt seam, drop the blobs it produces, repair blobs that overlap it (template subtraction), flag them `on_seam`. Also yields belt speed from seam sightings. |
| track | `vision/track.py` | Link blobs across frames in the belt frame (`belt_x = x - v*(t - t0)`, constant per part). Emits a finished `Track` when a part leaves view. |
| features | `vision/features/` | Per-observation size, shape and hole features, all in inches (via `PlaneMapper` only). |
| fuse | `vision/fusion.py` | Median and MAD of each feature over the track's valid frames. |
| classify | `classify/` | Compare fused features to the parts library ranges; verdict CONFIDENT / OUT_OF_SCOPE / UNRESOLVED. |
| decide | `decide/` | Group parts closer than `rework_gap_in` along the belt; apply the decision table. |
| schedule | `schedule/` | Compute the send time per group and fire it; log a `send` record. |

The pipeline core (`vision`, `classify`, `decide`, `schedule`, `output`, `pipeline`) does not
import Qt. Grading, report and bench read only the decision-log JSONL and the session folder.
Everything that can be tuned lives in `config/default.toml` with a matching schema in
`config.py`; unknown keys are rejected.

The decision table (brief): a lone part goes to its class bin if CONFIDENT, otherwise to MISC,
never to REWORK. A group (parts too close to separate) goes to a class bin only when every
member is CONFIDENT and the same class, to MISC only when every member is out of scope, and
otherwise to REWORK. A wrong REWORK costs a re-feed; a wrong class bin is contamination, so
every ambiguous case falls toward REWORK or MISC.

## Features that separate the hard pairs

Gray level is never used as an identity feature (the brief: it varies by part and lighting).
Features are geometric and in inches.

- **1/4 in washer vs 3/8 in nut.** Outer size is nearly identical (0.625 OD vs 0.650 across
  corners), so size alone does not separate them. The library separates them on:
  - `hole_kind`: the washer's hole shows the belt through it (code 1, bright); the nut's bore
    reads dark (code 2). It is a categorical median, so a split vote lands on 0.5/1.5 and sits on
    a range edge instead of passing.
  - `hex_harmonic`: the 6th radial harmonic of the contour. About 0.025 to 0.095 for a hex nut,
    about 0 for a round washer.
  - `circularity`: 0.85 to 0.97 for a hex, 0.95 to 1.05 for a circle.
  - `area_in2` and `minrect_short_in`: the washer's solid disc area (0.27 to 0.34) against the 3/8
    nut's hex ring (0.23 to 0.32) overlap a little, so area alone is only supporting evidence.
  Any one of hole kind, hex harmonic or circularity is enough to disqualify the wrong pose; the
  classifier requires that exactly one pose fits.
- **Washer vs nut generally**: same three features (`hole_kind`, `hex_harmonic`, `circularity`).
- **1/4 vs 3/8 of the same type**: size features (`area_in2`, `across_flats_in`,
  `minrect_short_in`, `minrect_long_in`; for bolts `shank_width_in`, `head_width_in`,
  `head_length_in`, `total_length_in`). The ranges are disjoint by design.
- **Bolt vs everything else**: `minrect_long_in` of about 1.0 in plus the shank and head profile.
- **Out-of-scope parts** (coin, 5/16 washer, a bolt standing up): their best pose score exceeds
  `out_of_scope_threshold`, so they go to MISC.
- Thread pitch (M6 vs 1/4-20 differ by 0.01 in) is not separable and is not built.

The classifier score is the worst feature's distance from its range centre, scaled so 0 is
the centre and 1 is the range edge. A required feature that is NaN means the pose cannot be
shown to fit, never a pass.

## Adding a new part

1. Add the value to `PartClass` in `contracts.py` (a contract change: it goes through the
   orchestrator, see `.agent-board/board.md`) and, for synthetic tests, a `PART_DIMS` entry in
   `synthetic.py`.
2. Add a library entry in `config/parts_library.yaml`: `display_name`, `output_code`, and one or
   more poses, each a `feature: [lo, hi]` map. Use feature names from
   `classify.library.FEATURE_NAMES` (a test keeps it equal to `vision.features.feature_names()`).
   Add a pose per way the part can lie (a nut flat, a nut on its side).
3. Check that the new ranges do not overlap an existing part's ranges on every feature that
   matters; overlap makes both UNRESOLVED (never contamination, but the parts go to MISC).
4. Bin routing: the decision table and `Output` need an entry for the new class.
5. Replace guessed ranges with measured ones. Run the part through `sorter run` or
   `replay --pipeline`, label it in `sorter grade`, then
   `sorter derive-ranges <graded sessions...>`. It writes a NEW file
   (`config/parts_library.derived.yaml`) so hand edits are never overwritten; review it and
   copy what you want into `parts_library.yaml` (or pass `--in-place`). Derived output has one
   pose called `measured`, padded by `--margin`; classes with no examples are copied from the
   base library. Merge `measured` into your hand-made poses rather than replacing them if you
   rely on poses the sessions did not show.

## Seam handling

The belt has a stitched diagonal seam. It is detected geometrically, so it never becomes a
part: blobs made of seam stitches are removed and the seam never blocks a group from closing.
A part overlapping the seam has its outline recovered by registering a learned seam template and
subtracting it; such a part is flagged `on_seam` and travels with that flag into fusion and the
log. The seam angle is in degrees from the travel axis (+x). Template-based repair needs the
template to have been learned from part-free passes first; until then the overlap is only
flagged.

## Timing model

- `Frame.t_capture_ns` is the raw driver timestamp on the monotonic clock. The pipeline subtracts
  camera latency (`timing.camera_latency_ms`) exactly once; nothing else corrects it, and replay
  preserves the raw stamps.
- All times are integer nanoseconds from an injected `Clock` (`MonotonicClock` live,
  `ManualClock` in replay and tests), so the same recording gives the same decision log.
- A group's reference time and lead-edge position come from its most downstream member.
  `send_time_ns` (`schedule/scheduler.py`):
  `t_send = t_ref + d_remaining / v - collector_lead`, where
  `d_remaining = (exit_x_in - x_lead_in_at_ref) + exit_to_belt_end_in`.
- The `Scheduler` thread queues one output per group and fires it at `t_send`. If the decision
  arrives after `t_send` it sends MISC with `LATE_DECISION`. The scheduler (not the adapter)
  writes the single `send` log record, because it knows the miss reason. Cross-thread types are
  frozen and the scheduler never touches vision objects.
- The 250 ms identify-time target is checked from the log by `sorter report` (latency from the
  last used frame's capture to the group decision) and by `sorter bench`.

## Processing budget (ADOPTED)

Parts are in view for 60 to 85 frames but about 10 are enough. Adopted settings
(`[pipeline]` in `config/default.toml`; rationale in `.agent-board/budget-proposal.md`):

- `frame_stride = 2`: process a frame only when `seq % frame_stride == 0`.
- `feature_stride = 3`: extract features on every 3rd processed valid observation.
- `max_feature_frames = 10`: at most 10 feature frames per track (config validation requires
  this to be at least `classify.min_frames` and `features.min_valid_frames`).
- Segmentation runs coarse-then-crop (`segment.coarse_scale = 2`; equivalence with 1 is tested).
- Replay blocks and never drops frames; any queue overflow is logged and counted as a failure.
- `sorter bench --compare-strides` is the accuracy guard: frame_stride 1 vs 2 and
  max_feature_frames 10 vs 20 on a deterministic synthetic stream (with near-miss out-of-scope
  parts); accuracy and contamination must not get worse under the adopted settings.

## Stationary-belt mode

`belt.mode = "stationary"` (`sorter run --stationary`) is for live tests with the belt off and
parts placed by hand. Moving mode is unchanged; the defaults and every moving code path are the
same, and a moving session log omits the new config keys so it stays byte-identical.

- **Speed.** v = 0 everywhere: belt frame = image frame, nothing is scheduled, no send records.
- **Validity** (`Tracker`, `stationary` set): an observation is valid when the part is fully in
  view, not merged, its centre moved less than `stationary.move_tol_in` since the last
  observation and its area is stable (`track.max_area_change`). No belt-speed test. A track
  finishes only when its part is removed (unseen for `stationary.missed_s` with no hand in
  view), never by leaving the view; only the last 60 observations are kept.
- **Hand / arm.** A blob that touches the image or belt edge and is larger than
  `stationary.max_part_area_in2` (default 1.0 in^2; the largest in-scope part, a bolt, is about
  0.35 in^2 and a hand is tens) is dropped before tracking: never a track, never a part. While
  one is in view the tracks it may cover are held (not finished) and no decision is made.
- **Measured.** A track is fused and classified once, when it has `pipeline.max_feature_frames`
  features (or `classify.min_frames` after `stationary.settle_s`) and its last `track.settle_frames`
  observations are valid. It is then locked and takes no more features: its class cannot flap. A
  track that never measures cleanly is measured as it is after `stationary.give_up_s`.
- **Decision.** `StationaryDecider` groups the measured parts present with the same gap rule as
  `Grouper` (`belt.rework_gap_in`) and the same rule table. When no hand is in view and no track
  is still pending, each group whose membership is new since the last decision is decided once:
  the new `part` records, then the `group` record, are written and the adapter announces it
  (`announce`, not `send`).
- **Moved / removed.** A locked part seen more than `stationary.relocate_in` from where it was
  measured, or back after a gap of 0.15 s with a different silhouette (IoU below 0.9), or whose
  area changed by more than twice `track.max_area_change` for `track.settle_frames` sightings,
  finishes: the blob starts a new track and is measured again. A part removed for `missed_s`
  finishes the same way. Either way its group is re-decided (new `group_id`).
- **Logs.** `part` records are as in moving mode. `group` records carry `"stationary": true`;
  `x_lead_in_at_ref` is the part's image position. Grading and the report read part records and
  crops as before; `LoggedGroup.sent_output` is `None`.

## Known limits

- **Stationary mode.** Two parts that touch are one silhouette and are decided as one part (they
  usually come out UNRESOLVED or MISC, not REWORK); keep them apart by a visible gap. Strong
  lighting flicker (about 50% dimmed frames) degrades the measurement; there is no flicker
  handling by decision, the fix is in hardware. The hand rule needs the arm to touch the image or
  belt edge.

- **Synthetic-only validation.** This session had no camera. Everything was built and tested
  with `synthetic.py` scenes, generated recordings and unit tests. Library ranges come from
  nominal and synthetic measurements and are not calibrated to the real optics. Real-camera
  checks (calibration, real-recording runs, lighting, real seam) are listed on the
  `hardware-pending` branch (`HARDWARE_PENDING.md`).
- **Acceptance scene:** end to end on the synthetic acceptance scene (30 parts) the pipeline
  gives 30/30 correct outputs with zero contamination, one track, decision and send per
  part; determinism holds.
- **Harsh random scene.** On a deliberately harsh 60-part scene (random bolt angles 0-360 deg,
  random cross-belt positions, seam present -- `bench stride_parts`) the pipeline scores about
  85% with **zero contamination**; the misses are the seam-interaction class below plus a few
  random-angle bolts and the occasional unresolved part, all failing safe to REWORK/MISC. Real
  absolute accuracy is owed at checkpoints 2-3 with a real camera and seam.
- **Unscorable parts report UNRESOLVED, not OUT_OF_SCOPE.** When every candidate pose scores
  non-finite (e.g. all required features read NaN), the classifier returns UNRESOLVED rather than
  OUT_OF_SCOPE. Both are safe -- neither sends the part to a class bin; for a lone part it means
  REWORK instead of MISC, for a group it only shifts REWORK vs MISC. A strict reading of the
  verdict rule would label it OUT_OF_SCOPE; this is a deliberate, harmless simplification in
  `classify/ranges.py`.
- **Bolt lying ON the seam (documented edge case).** A bolt whose body lies on the seam can fuse
  with the seam's stitch strip in `seam._repair`; the track is then MERGED, which is always
  UNRESOLVED, so the part goes to REWORK. This is safe (a re-feed, never contamination) and was
  accepted by the user. `tests/test_pipeline.py::test_bolt_on_seam_is_one_part_not_split` is an
  expected failure that documents it. The diagnosis is parked on `hardware-pending` for a re-check
  on a real seam, where the stitch appearance may differ.
- **Bench numbers need the Dell.** The timing figures from `sorter bench` on this Linux box
  (2 cores, shared) say nothing about the 250 ms target on the Dell target laptop; acceptance
  numbers must be measured there. `sorter replay --compare` is the Mac-vs-Dell consistency check.
- **Steady lighting is assumed.** Lighting flicker is to be fixed in hardware; the pipeline has
  no flicker rejection (only a slow gain tracker for gradual drift). Recordings made before that
  fix must be judged on undimmed frames.
- **Belt speed, lead time, camera latency and runway are uncalibrated placeholders** until
  measured on the rig. Serial output (M4) and the Linux camera backend (M5) are not built.
- Parts that are touching or closer than `rework_gap_in` are not split; they go to REWORK by design.
