# 002 contracts module, config sections, fakes

Status: DONE
Dependencies: 001
Branch: `task/002-contracts` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/contracts.py (new), src/hwsorter/config.py, config/default.toml, tests/fakes.py (new), tests/test_contracts.py (new), src/hwsorter/vision/__init__.py, src/hwsorter/vision/features/__init__.py (empty package marker only), src/hwsorter/classify/__init__.py, src/hwsorter/decide/__init__.py, src/hwsorter/schedule/__init__.py, src/hwsorter/output/__init__.py (empty package markers only)

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it unless this IS task 002).
Implement contracts-spec.md exactly as `src/hwsorter/contracts.py`: enums, frozen dataclasses, Protocols (FrameStage-free; Classifier, OutputAdapter). Pure stdlib + numpy, no Qt, no cv2.
- Check `synthetic.PART_DIMS` keys and make `PartClass` values match; record the actual names in your task file.
- Check `Frame.t_capture_ns` semantics in frames.py / capture.py / recording/replay.py and record the answer (is it arrival time? latency-corrected?) in the task file.
- Add config sections with sensible defaults + doc comments to config.py and default.toml: `segment` (deviation threshold, edge threshold, min_area_in2, close kernel px), `seam` (angle_deg, width_in, enabled), `track` (gate_in, settle_frames, max_missed_frames, speed_tolerance), `classify` (confidence_threshold, out_of_scope_threshold, min_frames, library_path = "config/parts_library.yaml"), `scheduler` (spin_ms, enabled), `pipeline` (decision_log_dir, crop_dir). Reuse existing BeltConfig.rework_gap_in / TimingConfig; don't duplicate.
- Define the decision-log record schema (field names for kinds "part", "group", "send", "session_start", "session_end") as TypedDicts or documented dataclasses in contracts.py, including `schema_version`.
- `tests/fakes.py`: builders `make_blob`, `make_track`, `make_part_result`, `make_fused`, `make_classification` with sensible defaults.
- Create empty package `__init__.py` files for vision, vision/features, classify, decide, schedule, output so later parallel tasks don't collide creating them.
- tests/test_contracts.py: construct each type, config loads with new sections, unknown keys still rejected.

## Verification Output
Commit: af6d705 on task/002-contracts. Worktree: <REPO_ROOT>/.claude/worktrees/agent-afe8c7abfce3f18dc

| Command | Exit | Output (tail) |
| --- | --- | --- |
| `uv run pytest` | 0 | `41 passed in 11.12s` |
| `uvx ruff@0.17.5 check src tests` | 0 | `All checks passed!` |
| `uvx ruff@0.17.5 format --check src tests` | 0 | `40 files already formatted` |
| `uv run sorter --help` | 0 | usage + subcommands incl. calibrate; options --config, -v |

Q1 PartClass vs PART_DIMS: in-scope keys are bolt_025, bolt_0375, nut_025, nut_0375, washer_025, washer_0375 (PART_KINDS; PART_DIMS also has out-of-scope coin, washer_0312). PartClass values equal these exactly (test enforces). Output.for_class() added.

Q2 Frame.t_capture_ns: raw driver timestamp on the host monotonic clock (avfoundation falls back to arrival time if the driver clock is not monotonic). NOT latency-corrected. Synthetic source sets t_capture=t_arrival. ReplaySource/recording store preserve recorded t_capture_ns unchanged (loop mode only adds a lap offset). So the pipeline must subtract timing.camera_latency_ms once; types in contracts.py carry the corrected value. Replayed recordings are in the original session's monotonic domain, so pipeline/bench should rebase or use ManualClock.

Deviations from contracts-spec.md: none in types. Additions: Output.for_class(), DECISION_LOG_SCHEMA_VERSION, TypedDicts PartRecord/GroupRecord/SendLogRecord/SessionStartRecord/SessionEndRecord, runtime_checkable Protocols, default values on some fields (Blob.on_seam, Observation.valid, FeatureVector.valid, FusedFeatures.on_seam, Classification fields, PartResult.crop_ref, SendRecord.miss), numpy-holding dataclasses use eq=False. Config defaults are my choices (segment deviation 0.12 etc.). tests/fakes.py is imported as `from fakes import ...` (tests dir on pytest rootdir path works).


### Round 3
Commit 84c89f5 on `task/002-contracts`. Worktree: <REPO_ROOT>/.claude/worktrees/agent-a98d00b5ee79bf254

| Command | Exit | Tail |
| --- | --- | --- |
| `uv run pytest` | 0 | `47 passed in 11.12s` |
| `uvx ruff@0.17.5 check src tests` | 0 | All checks passed! |
| `uvx ruff@0.17.5 format --check src tests` | 0 | 40 files already formatted |
| `uv run sorter --help` | 0 | usage: sorter [-h] [--config CONFIG] [-v] {camera-info,gui,record,replay,recordings,calibrate} ... |

Hole measurement (synthetic.py, seam off, noise 0, specks 0; ratio = render with part / render without, 1.0 = belt level):
| part | centre | ring r 0.16-0.28 in | min | max |
| --- | --- | --- | --- | --- |
| washer_025 | 1.00 | 0.40 | 0.39 | 1.00 |
| washer_0375 | 1.00 | 0.71 | 0.60 | 1.00 |
| nut_025 | 0.21 | 0.76 | 0.21 | 1.00 |
| nut_0375 | 0.21 | 0.58 | 0.21 | 1.00 |
| bolt_025 | 1.03 | 0.89 | 0.28 | 1.41 |
| bolt_0375 | 1.09 | 0.80 | 0.29 | 1.42 |

Thresholds: hole_bright_threshold 1.05 -> 0.90 (washer hole 1.00, bodies <= ~0.76 in the ring); hole_dark_threshold 0.85 -> 0.30 (nut bore 0.21, bodies >= 0.40). Changed in config.py and config/default.toml together. Bolt facets (up to 1.4) are only relevant for elongated parts, which skip hole features.

Review item -> fix:
1. DETERMINISM_IGNORE_KEYS now includes `t_actual_ns`; comment documents per-kind comparison; new `normalise_for_compare(records) -> dict[kind, list[record]]` (drops ignored kinds/keys, groups by kind, file order within kind); tests `test_normalise_for_compare_groups_by_kind_and_ignores_timing` and updated key assertion.
2. Scheduler-writes-send rule documented on OutputAdapter, SendRecord and SendLogRecord (adapters never log it; ADAPTER_ERROR if send raised).
3. `StageSummary` TypedDict {count, mean, p50, p95, p99, max} in ms; timings comment says session_end stages_ms maps stage -> StageSummary.
4. Hole thresholds 0.90 / 0.30 as above.
Optional done: 7 (fakes keys now minrect_long_in/minrect_short_in), 8 (SeamObs: x_center_px at mid row; width_px perpendicular, ~0.26 in along x), 9 (latency_ms semantics: part at group close, ManualClock = belt time; group/send defined), 10 (`parse_flags` + roundtrip test).
Optional skipped: 5 (frame-count knob validator/fuse below min_valid_frames), 6 (contracts-spec.md is board-owned; part_record helper not added).

## Review Notes
Reviewer re-run in worktree agent-afe8c7abfce3f18dc at af6d705 (diff vs develop: 11 files, all in scope).

| Command | Exit | Tail |
| --- | --- | --- |
| `uv run pytest` | 0 | 41 passed |
| `uvx ruff@0.17.5 check src tests` | 0 | All checks passed! |
| `uvx ruff@0.17.5 format --check src tests` | 0 | 40 files already formatted |
| `uv run sorter --help` | 0 | usage printed |

Also checked: every spec enum, dataclass and field is present. Cross-thread types (GroupDecision, ScheduledOutput, SendRecord) are frozen and use tuples. default.toml matches the pydantic defaults exactly (script diff of model_dump vs tomllib: no differences). Unknown keys are still rejected (extra="forbid", tested). spin_ms 4.0 matches MonotonicClock's 4 ms. rework_gap_in reuses BeltConfig (1.0). `from fakes import` works under plain `uv run pytest` (prepend import mode, tests/ has no `__init__.py`).

CHANGES REQUIRED:

REQUIRED
1. contracts.py:24 `from .calibration.store import RoiCalibration` loads cv2 transitively: `import hwsorter.contracts` puts cv2 into sys.modules (verified). This breaks "pure stdlib + numpy, no cv2", and grading/report pay for it. Fix: import it under `if TYPE_CHECKING:`. The annotation is already a string because of `from __future__ import annotations`.
2. config.py:93-130 + default.toml: tunables that tasks 004-011 name are missing. Those workers cannot edit config.py, so each gap becomes a blocker or a hard-coded constant. Add these (names are suggestions):
   - `segment`: `gain_alpha` (slow gain tracker rate, 004), `shadow_max_depth` or equivalent soft-shadow rejection threshold (004), an explicit sharp-edge mask gradient threshold, and a sub-pixel smoothing/refine setting (004). Also make `edge_threshold`'s meaning unambiguous: the toml says "sub-pixel edge placement", the pydantic comment says "hysteresis", and 004 wants a "sharp-edge mask".
   - `seam`: template learning (`template_frames` or `template_alpha`), speed estimate smoothing (`speed_window`), and detection tolerances (`angle_tol_deg`, `width_tol_in`) (005).
   - `track`: `max_area_change` (fractional, for the "stable outline" validity rule, 006) and `min_frames` for the FEW_FRAMES flag. The other option is to document that FEW_FRAMES uses `classify.min_frames`.
   - new `[features]` section (007): `elongated_min_aspect` (when the bolt profile is computed versus NaN), `hole_bright_threshold`, `hole_dark_threshold`, `hole_min_diameter_in`, and `radial_samples` for hex_harmonic.
   - `scheduler`: `screen_log_keep` (the "last N" in 010).
   - `pipeline`: `crop_pad_in`, `save_crops`, `save_full_frames` (011).
3. config.py:102-103 / default.toml:72-73: the seam defaults contradict the brief and synthetic.py. synthetic.py:181-185 draws the seam at 50 deg from the travel (x) axis, which is 40 deg from vertical, with a 0.2 in band. The defaults are `angle_deg=0.0` (a vertical, non-diagonal seam) and `width_in=0.25`. Set the defaults to match the synthetic seam so 005's tests pass without overrides. Also state the angle reference and sign in both the config comment and `SeamObs` (contracts.py:106-115), e.g. "degrees from +x toward +y (image y down)".
4. contracts.py:197-220: GroupDecision, ScheduledOutput and SendRecord have no docstrings, but they cross the 009/010/011 boundary. Document:
   - for `x_lead_in_at_ref`/`t_ref_ns`: which member they come from (the most downstream member, max `belt_x_lead_in`), and that the value is belt-plane inches at `t_ref_ns`, not belt-frame;
   - that `t_closed_ns` and `t_send_ns` are in the Clock domain;
   - what `t_actual_ns` means on a late send;
   - the `rule` vocabulary (e.g. "lone_confident", "lone_misc", "group_same_class", "group_all_oos", "rework"), so the report can tabulate it.
5. contracts.py:240-268 decision-log serialisation is underspecified, so 011, 012, 013 and 015 can diverge:
   - (a) EventLog uses `json.dumps` with `allow_nan` on. `Classification.runner_up_score` defaults to `inf` and features use NaN, so the log gets non-standard `Infinity`/`NaN` (verified). PartRecord types them `float | None` but never states the rule. State "NaN and +-inf are written as null" (or add a `to_record()` helper).
   - (b) Determinism: the spec (invariant 10) excludes only `timings`, but EventLog also writes a top-level `t_ns` and a `log_open` record with `wall_time`, and `session_start.session/source` may carry names or paths. List exactly what the replay diff ignores.
   - (c) `flags`: say "names in TrackFlag definition order" (or add a `flag_names()` helper next to the enum).
6. contracts.py:247-268 PartRecord is missing fields that grading and report need:
   - a full-frame ref (`frame_ref: str | None` and/or `ref_seq: int`). The brief's grading step 1 says "crop and its full frame", and 011 says it saves a full-frame reference, but the record has only `crop_ref`.
   - `y_min_in`/`y_max_in`. Side-by-side parts have overlapping belt_x intervals, so 012's rematch-by-overlap cannot separate them using x alone.
   - `t_first_ns`/`t_last_ns` (the last frame used is the start point of the brief's latency metric).
   Also state that group membership comes only from GroupRecord.member_track_ids.
7. contracts.py:310-320 SessionEndRecord: 013 must report fps, drops and deadline misses, but there is no `n_dropped` and no duration. Add `n_dropped` and `duration_ns` (or document the keys). Also document the minimum `timings` keys, e.g. `{"stages_ms": {stage: ms}, "latency_ms": float}` on part records and per-stage summaries on session_end. Otherwise 011 (writer) and 013/015 (readers) guess independently. `timings: dict[str, Any]` alone is not a contract.

OPTIONAL
8. tests/fakes.py:96-117 `make_part_result` cannot override `fused` or `classification`: passing `fused=` raises "multiple values for keyword argument" (verified). Accept them as optional params. `make_classification(Verdict.OUT_OF_SCOPE)` keeps score 0.3, which contradicts invariant 7 (OOS means best score > 1.5). Use verdict-consistent default scores.
9. tests/fakes.py: add `make_prepared`, `make_observation`, `make_feature_vector`, `make_group_decision` and `make_scheduled_output`. 004/005/007/010 will otherwise each write their own.
10. `from fakes import` relies on prepend import mode. Add `pythonpath = ["tests"]` to `[tool.pytest.ini_options]` (orchestrator-approved scope change) to make it robust.
11. classify: add `derive_margin` (008 `derive_ranges`). Document that `library_path` and `pipeline.*_dir` resolve against project root and the session folder respectively.
12. SessionStartRecord: include the calibration actually used (px_per_in, measured speed, machine) and `exit_x_in`, so report and rerun don't need the calibration file.
13. classify.min_frames=3 is low given ~100 frames per part in view at 4 in/s. Consider 5-8.
14. `PipelineSnapshot` is not in contracts. That is fine because 011 owns it and 014 depends on 011, but note it on the board.

### Round 2
Fresh reviewer re-ran everything in worktree agent-a9788a2cc7e6ecbd9 at fab8f07 (`task/002-contracts`). Diff vs develop: 12 files, all in scope, plus `pyproject.toml` (the pythonpath change approved in round-1 item 10).

| Command | Exit | Tail |
| --- | --- | --- |
| `uv run pytest` | 0 | `45 passed in 11.46s` |
| `uvx ruff@0.17.5 check src tests` | 0 | All checks passed! |
| `uvx ruff@0.17.5 format --check src tests` | 0 | 40 files already formatted |
| `uv run sorter --help` | 0 | usage: sorter [-h] [--config CONFIG] [-v] {camera-info,gui,record,replay,recordings,calibrate} |

Note: this task file was not updated for round 2. Verification Output still cites af6d705, the old worktree and 41 tests, and there is no fix mapping. The table above replaces it.

Round-1 REQUIRED items, checked in the code:
1. Fixed. RoiCalibration is imported under `TYPE_CHECKING`. I verified in a fresh interpreter that `cv2` and PySide6 are not in `sys.modules` after `import hwsorter.contracts`, and `test_import_contracts_is_light` covers this.
2. Fixed. All requested keys exist: segment gain_alpha, shadow_max_depth, edge_gradient_threshold and subpixel_smooth_px, with edge_threshold now unambiguous; seam template/speed/tolerances; track max_area_change and min_frames; the `[features]` section; screen_log_keep; crop_pad_in, save_crops and save_full_frames. The values are a separate problem (item 4 below).
3. Fixed. Seam angle 50 and width 0.2 match synthetic.py:181-185, whose direction (cos50, sin50) runs from +x toward +y. The convention is stated in SeamConfig, default.toml and the SeamObs docstring.
4. Fixed. GroupDecision, ScheduledOutput and SendRecord have docstrings, and `RULES` gives the rule vocabulary.
5. Partly fixed. (a) Done: `to_log_dict` writes NaN and inf as null, and a test runs `json.dumps(allow_nan=False)`. (c) Done: `flag_names` returns names in definition order. (b) The ignore list exists but is incomplete; see item 1 below.
6. Fixed. PartRecord has frame_ref, ref_seq, y_min_in/y_max_in and t_first_ns/t_last_ns, and the comment states that group membership comes only from GroupRecord.
7. Partly fixed. SessionEndRecord has n_dropped and duration_ns, and minimum `timings` keys are documented, but they are too thin for 013; see item 3 below.

Board decisions: all implemented. Seam angle from +x: yes. Null for non-finite values through `to_log_dict`: yes. `DETERMINISM_IGNORE_KEYS`/`KINDS`, including log_open: yes. The most-downstream member supplies `t_ref_ns`/`x_lead_in_at_ref`, as stated in the GroupDecision docstring: yes. Frame.t_capture_ns semantics are in the module docstring: yes.

The worker's additions:
- `pythonpath = ["tests"]` is fine. It is approved, harmless, and makes `from fakes import` work regardless of import mode.
- `track.min_frames` and `classify.min_frames` (both 5): acceptable, not blocking. They count slightly different things: the tracker counts valid Observations to set FEW_FRAMES, and the classifier counts `FusedFeatures.n_frames` for invariant 7. Each worker therefore has exactly one key to read, and with equal defaults no outcome differs. The risk is drift. If someone tunes one key and not the other, a part can be UNRESOLVED for too few frames with no FEW_FRAMES flag (or the reverse), and the report's flag breakdown then misleads. `features.min_valid_frames = 3` is a third knob for the same idea, and it is below both, so it never takes effect. See OPTIONAL 5.

CHANGES REQUIRED:

REQUIRED
1. contracts.py:280-289 (determinism): the ignore set still assumes the whole log is deterministic. Two things written from the scheduler thread are not. (a) `send.t_actual_ns`: under ManualClock, `sleep_until_ns` (clock.py:60-62) wakes on some later `advance_to`. In a `--fast` replay the pipeline thread may already have advanced several frames, so `now_ns()` at send time depends on thread scheduling. (b) Line order: "send" lines are written by the scheduler thread while "part" and "group" lines come from the pipeline thread, so the interleaving in the file is not reproducible. As written, 011's determinism test and 015's `replay --compare` will be flaky, and 011 cannot fix this inside 010's scheduler. Fix: add `t_actual_ns` to `DETERMINISM_IGNORE_KEYS` (send-time error in a ManualClock replay is meaningless anyway; `miss` is decided at submit and stays deterministic). Also state that the comparison is per kind: the sequence of records of each kind, in file order, after the ignores, not a whole-file line diff. Update the test.
2. contracts.py:264-270 and 362-372: say who writes the "send" record. `OutputAdapter.send(o, t_actual_ns)` never receives the `miss` reason, yet `SendLogRecord` requires `miss`. Task 010's text says "ScreenLogAdapter logs a send record", while the spec says the pipeline writes it. Without a rule, 010 and 011 will either duplicate the record or omit it, and a replay with MockSerialAdapter would log no sends at all. State: "The Scheduler writes exactly one `send` record per ScheduledOutput after `adapter.send` returns (ADAPTER_ERROR if it raised). Adapters do I/O only and never write the decision log." The orchestrator should add the same line to 010.
3. contracts.py:313-315: the minimum `stages_ms` summary for session_end is `{mean, p95, max}`, but 013 must report p50/p95/p99/max per stage and reads only the log. Make it `{"mean","p50","p95","p99","max"}`. Otherwise 013 relies on keys 011 does not promise.
4. config.py:127-128 and default.toml:93-94: the hole-threshold defaults contradict synthetic.py, the same class of problem as round-1 item 3. I measured flat values (render with part ÷ render without, seam/noise/specks off): washer hole centre 1.00 (the belt shows through), nut bore 0.21, nut and washer bodies 0.40-0.61, bolt facets up to 1.45, darkest bolt pixel 0.28. With `hole_bright_threshold = 1.05` a see-through washer hole is never "bright". With `hole_dark_threshold = 0.85` the whole body of every nut and washer counts as a "dark bore". 007's required test (washer_025 vs nut_0375 by hole_kind) cannot pass on defaults, and 011's end-to-end test runs on defaults. Neither task may edit config. Fix: about 0.90 and about 0.30, or define both relative to the belt or the part body and say so in the comment. An acceptable alternative is a board decision that lets the owning task retune default values (not keys) in config.py and default.toml together. That would also cover every other default 002 picked without data.

OPTIONAL
5. Frame-count knobs: either add a `SorterConfig` model validator requiring `track.min_frames == classify.min_frames`, or document classify.min_frames as authoritative and FEW_FRAMES as diagnostic. Also define what `fuse()` returns below `features.min_valid_frames` (all-NaN medians?), or drop that key.
6. contracts-spec.md:38 still says "serialised with `dataclasses.asdict`". The orchestrator should change it to `to_log_dict` and the flat PartRecord. Note that `to_log_dict(PartResult)` produces nested `fused`/`classification` dicts, not the PartRecord shape. A `part_record(track, result, ...) -> PartRecord` helper, or just a sentence, would stop 011 from writing the nested form.
7. tests/fakes.py:91 and :160: the default feature keys `length_in`/`width_in` are not 007's names (`minrect_long_in`/`minrect_short_in`). Align them so 008's tests built from fakes look like real fused vectors.
8. contracts.py:114-125 SeamObs: say at which image row `x_center_px` is taken, and whether `width_px` and `seam.width_in` are measured across the band (synthetic: perpendicular) or along x (0.2/sin50 ≈ 0.26 in).
9. contracts.py:313-314: part `latency_ms` runs "to the group decision", so part records can only be written at group close. Say so. Under ManualClock this measures belt time, not compute time. Also define `latency_ms` for group and send records, or drop it there.
10. A `parse_flags(names) -> TrackFlag` next to `flag_names`, for 013's `--rerun` (re-classify from logged median/spread/flags).

### Round 3
Fresh reviewer re-ran everything in worktree agent-a98d00b5ee79bf254 at 84c89f5 (`task/002-contracts`). Branch-only diff (`develop...84c89f5`): 12 files, all in scope, plus the approved `pyproject.toml` pythonpath change. `git merge-tree` against current develop (which now includes 003) is clean.

| Command | Exit | Tail |
| --- | --- | --- |
| `uv run pytest` | 0 | `47 passed in 10.99s` |
| `uvx ruff@0.17.5 check src tests` | 0 | All checks passed! |
| `uvx ruff@0.17.5 format --check src tests` | 0 | 40 files already formatted |
| `uv run sorter --help` | 0 | usage: sorter [-h] [--config CONFIG] [-v] {camera-info,gui,record,replay,recordings,calibrate} ... |

The import is still light: after `import hwsorter.contracts`, neither cv2 nor PySide6 is in `sys.modules`.

Round-2 REQUIRED items, checked in `git diff fab8f07..84c89f5`:
1. Fixed. `t_actual_ns` is in `DETERMINISM_IGNORE_KEYS`. The comment block says the comparison is per kind and never a whole-file diff. `normalise_for_compare` drops ignored kinds, drops ignored keys at any depth, and groups records by kind in file order. The new test covers different interleavings (equal) and swapped order within a kind (not equal). This matches the board decision at board.md:40.
2. Fixed. The Scheduler is stated as the only writer of the "send" record, exactly one per ScheduledOutput, with ADAPTER_ERROR if the send raised. The rule appears on SendRecord, OutputAdapter and SendLogRecord, consistent with board.md:40.
3. Fixed. The `StageSummary` TypedDict has {count, mean, p50, p95, p99, max} in ms, and the session_end `stages_ms` comment refers to it.
4. Fixed. The defaults are hole_bright 0.90 and hole_dark 0.30, identical in config.py and default.toml, and the comments state the part/belt basis. They match both the round-2 measurements and the worker's table: washer hole 1.00, nut bore 0.21, bodies 0.40 or above.

Regressions: none. The OPTIONAL changes are also correct. Fakes now use `minrect_long_in`/`minrect_short_in`. The SeamObs row and width conventions are stated. `latency_ms` is defined for part, group and send. `parse_flags` is added with a roundtrip test.

APPROVE

OPTIONAL (for the orchestrator to carry to the owning tasks)
1. 010/015: `normalise_for_compare` keeps "send" records in file order. In a `--fast` ManualClock replay, the order of sends that are due together can still depend on thread timing, for example when a LATE_DECISION item is submitted while other sends are already due. To prevent flakiness, 010 should pop due items strictly by `(t_send_ns, group_id)`, or the compare step should sort "send" records by `group_id`.
2. 007: the darkest bolt pixels (0.28-0.29) are just below `hole_dark_threshold` 0.30. This is harmless only because elongated parts skip hole features. 007 should keep that gate, and should apply `hole_min_diameter_in` before it classifies a region as a bore.
3. Round-2 OPTIONAL 5 and 6 remain open: the frame-count knob drift and `fuse()` below `min_valid_frames`, plus updating the contracts-spec.md `asdict` text to `to_log_dict` and the flat PartRecord. Both belong to the board or orchestrator and to 006/007/011.
