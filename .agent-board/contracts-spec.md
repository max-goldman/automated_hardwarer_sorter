# M2/M3 contracts spec (from advisor, adopted by orchestrator)

Task 002 implements this in `src/hwsorter/contracts.py` and freezes it. Changes only via orchestrator (board.md).
Unverified point to check in 002: whether `Frame.t_capture_ns` is already latency-corrected (see invariant 2), and how `recording/replay.py` stamps times.

## Enums
- `PartClass(str, Enum)`: `bolt_025, bolt_0375, nut_025, nut_0375, washer_025, washer_0375` (match `synthetic.PART_DIMS` keys; adapt names to whatever those keys actually are and note it here).
- `Output(str, Enum)`: six PartClass values + `MISC`, `REWORK`.
- `Verdict`: `CONFIDENT | OUT_OF_SCOPE | UNRESOLVED`.
- `TrackFlag(Flag)`: `ON_SEAM, MERGED, UNSETTLED, TOUCHES_EDGE, FEW_FRAMES`.
- `MissReason`: `NONE | LATE_DECISION | ADAPTER_ERROR`.

## Data types (frozen dataclasses unless noted)
| Type | Owner | Fields |
| --- | --- | --- |
| `Prepared` | segment | `seq`, `t_capture_ns`, `flat: float32 (H,W)` belt ~1.0, `roi: RoiCalibration`, `gain: float` |
| `Blob` | segment | `contour_px: float32 (N,2)` full-image px (x,y), outer silhouette, sub-pixel; `mask_bbox_px: (x0,y0,x1,y1)`; `area_px`; `touches_edge: bool`; `on_seam: bool=False` |
| `SeamObs` | seam | `present`, `x_center_px`, `angle_deg`, `width_px`, `band_px: (4,2)`, `t_capture_ns` |
| `Observation` | track | `seq`, `t_capture_ns`, `blob`, `contour_in: (N,2)` belt-plane inches via `PlaneMapper.px_to_in`, `valid: bool` |
| `Track` (mutable) | track | `track_id: int` (monotonic per session), `observations`, `flags: TrackFlag`, `belt_x_lead_in`, `belt_x_trail_in`, `y_min_in`, `y_max_in`, `t_first_ns`, `t_last_ns`, `finished: bool` |
| `FeatureVector` | features | `values: dict[str,float]` (in, in², deg, unitless; NaN = n/a), `valid: bool` |
| `FusedFeatures` | fusion | `median: dict[str,float]`, `spread: dict[str,float]` (MAD), `n_frames: int`, `on_seam: bool` |
| `Classification` | classify | `verdict`, `part_class: PartClass|None` (only when CONFIDENT), `pose: str|None`, `score` (worst-feature fit, 0 perfect, 1 range edge), `runner_up_score`, `worst_feature: str|None` |
| `PartResult` | pipeline | `track_id`, `fused`, `classification`, `flags`, `belt_x_lead_in`, `belt_x_trail_in`, `t_ref_ns`, `x_lead_in_at_ref`, `crop_ref: str|None` |
| `GroupDecision` | decide | `group_id: int`, `member_track_ids: tuple[int,...]`, `output: Output`, `rule: str`, `t_ref_ns`, `x_lead_in_at_ref`, `t_closed_ns` |
| `ScheduledOutput` | schedule | `group_id`, `output`, `member_track_ids`, `t_send_ns` |
| `SendRecord` | schedule | `scheduled: ScheduledOutput`, `t_actual_ns`, `miss: MissReason` |

## Signatures
- segment `vision/segment.py`: `class Preprocessor(flat: np.ndarray|None, roi, cfg)` `.apply(frame) -> Prepared`; `segment(p: Prepared, cfg) -> list[Blob]`. Reuse `calibration.flatfield.apply_flatfield`.
- seam `vision/seam.py`: `class SeamHandler(cfg, template=None)` `.process(p, blobs) -> tuple[list[Blob], SeamObs|None]` (removes seam blob, repairs overlapping blobs, sets `on_seam`); `.belt_speed_in_s() -> float|None`.
- track `vision/track.py`: `class Tracker(mapper: PlaneMapper, speed_in_s, cfg)` `.update(seq, t_capture_ns, blobs) -> list[Track]` (newly finished), `.live() -> list[Track]`, `.entry_belt_x_in(t_ns) -> float`.
- features `vision/features/__init__.py`: `@register(name)` on `fn(contour_in, prepared, blob) -> dict[str,float]`; `extract(obs, prepared) -> FeatureVector`; `feature_names() -> tuple[str,...]`.
- fusion `vision/fusion.py`: `fuse(track, vectors: list[FeatureVector]) -> FusedFeatures`.
- classify `classify/`: `class Classifier(Protocol)` `classify(fused, flags) -> Classification`; `load_library(path) -> PartsLibrary`; `RangeClassifier(library, cfg)`.
- decide `decide/`: `class Grouper(rework_gap_in)` `.add(part: PartResult)`, `.advance(entry_belt_x_in, t_ns) -> list[list[PartResult]]`; pure `decide(members) -> Output`; `class Decider` wraps both -> `list[GroupDecision]`. `correct_output(labels_in_group: Sequence[PartClass|None]) -> Output` in `decide/rules.py` (None = out of scope), reused by grading and report.
- schedule/output: `send_time_ns(d: GroupDecision, exit_x_in, timing: TimingConfig, speed_in_s) -> int`; `class Scheduler(clock, adapter, log)` `.submit()`, `.pending()`, `.start()`, `.stop()`; `class OutputAdapter(Protocol)` `send(o, t_actual_ns)`, `close()`; `ScreenLogAdapter`, `MockSerialAdapter` (`.sent: list`).
- pipeline `pipeline.py`: `class Pipeline(cfg, cal, clock, adapter, log)` `.process(frame) -> PipelineSnapshot`, `.flush()`, `.stage_timings()`. Decision log via existing `EventLog`, kinds `"part"`, `"group"`, `"send"`, serialised with `dataclasses.asdict`.
- grading, report, bench + replay regression: read only the decision-log JSONL and the session folder; never import vision internals.

## Invariants
1. **Image frame:** full-image px (x right, y down), never ROI-relative. Parts travel +x.
2. **Clock:** all times are int ns in the injected `Clock`'s monotonic domain. Only the pipeline corrects `t_capture_ns` for camera latency, exactly once. Replay and bench inject `ManualClock` or rebase recorded times. Only `MonotonicClock` calls `time.*`. `EventLog.log` stamps its own `time.monotonic_ns()`, so records carry explicit `t_*_ns` fields.
3. **Belt plane:** inches via `ScaleCalibration`, +x along travel, +y down, origin under the image centre. Conversion only through `PlaneMapper`; nobody multiplies by `px_per_in`.
4. **Belt frame:** `belt_x = x_in - v*(t_capture_ns - t0_ns)/1e9`, t0 = session start. Constant per part. Larger belt_x = further downstream. Lead = max x, trail = min x.
5. **Gap:** `trail(downstream) - lead(upstream)` along x only, clamped at 0. Side by side or overlapping = 0. Link when gap < `rework_gap_in`.
6. **Group closure:** closes when `entry_belt_x_in <= group_trail - rework_gap_in`, all members finished, no live track in that span. The seam never counts as a part or blocks closure. `flush()` closes everything.
7. **Verdicts:** CONFIDENT = one pose within threshold, no other part fits, `n_frames >= min_frames`, no MERGED/UNSETTLED. OUT_OF_SCOPE = best score > `out_of_scope_threshold`. UNRESOLVED otherwise; MERGED always UNRESOLVED.
8. **Decision table:** a lone part goes to its class bin if CONFIDENT, else MISC, never REWORK. A group goes to a class bin only if all members are CONFIDENT and the same class, to MISC only if all are OUT_OF_SCOPE, else REWORK.
9. **Timing:** `d_remaining = (exit_x_in - x_lead_in_at_ref) + timing.exit_to_belt_end_in`, `t_send = t_ref + d_remaining/v - collector_lead`. One output per group. If not submitted by t_send, send MISC with LATE_DECISION.
10. **Determinism and threading:** no wall-clock or random dependence, so the same recording gives a byte-identical decision log apart from a separate `timings` key. The scheduler thread never touches vision objects. Cross-thread types are frozen.
