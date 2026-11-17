# Agent Board: Hardware Sorter Vision Program

Main checkout: `<REPO_ROOT>` (placeholder: substitute the absolute path of your clone, e.g. the output of `git rev-parse --show-toplevel`, whenever you brief a subagent; workers need real absolute paths).
Board (absolute): `<REPO_ROOT>/.agent-board/`. Since the end of session 2 the board is TRACKED in git on `develop` (it used to be gitignored), so it travels with clones. Workers write their task files in the MAIN checkout (not their worktree copy); the orchestrator commits board changes on `develop` from time to time, separately from merges.
Base branch: `develop` (Milestone 1 baseline commit `3591145`). Workers branch from `develop`; orchestrator merges into `develop`. Never touch `main`.

Requirements: `descriptions/Hardware Sorter Vision Program Project Brief.md`
Approved architecture + milestone plan: `initial_progress.md` (section "Approved plan (in full)").

## Exact commands (run from the repo root or worktree root)

| Purpose | Command | Pass criterion |
| --- | --- | --- |
| Setup | `uv sync` | exit 0 |
| Tests | `uv run pytest` | exit 0, all pass |
| Lint | `uvx ruff@0.17.5 check src tests` | exit 0 (gate active since task 001) |
| Format check | `uvx ruff@0.17.5 format --check src tests` | exit 0 |
| Import smoke | `uv run sorter --help` | exit 0 |

No build step (pure Python, hatchling). Tests must run headless (Qt tests use offscreen platform already).

## Rules for every subagent
- Pipeline core (`src/hwsorter/vision`, `classify`, `decide`, `schedule`, `output`, `pipeline`) must NOT import Qt.
- Every tunable goes in `config/default.toml` + the pydantic schema in `src/hwsorter/config.py` (unknown keys are rejected, so add schema fields with the toml keys).
- No hard-coded scale, belt speed, gap, or timing constants in code.
- Use `src/hwsorter/synthetic.py` for test scenes; no real camera in tests.
- Commit on your own branch only. Paste every verification command, its exit code, and the last ~50 lines of output into your task file.

## Architecture decisions
- Pipeline stages and module layout follow `initial_progress.md` "Approved plan".
- **Contracts are frozen in `src/hwsorter/contracts.py`** (task 002). The full spec is in `<REPO_ROOT>/.agent-board/contracts-spec.md`. Every worker reads it. Workers never edit `contracts.py` or `config.py` after 002; if a contract change is needed, write it in your task file under "Contract change request" and set BLOCKED.
- Task 002 also adds all M2/M3 config sections (Segment, Seam, Track, Classify, Scheduler) to `config.py` + `config/default.toml`, and `tests/fakes.py` builders, so later tasks don't touch shared files.
- Grading, report and bench read only the decision-log JSONL and the session folder, never vision internals.
- **Lighting (user decision, 2026-11-17): assume STEADY lighting.** The lighting flicker will be fixed in hardware later. Do not design, tune or test for per-frame flicker. No flicker in synthetic test scenes; no flicker-rejection logic or thresholds sized for dimmed frames. The slow gain tracker for gradual drift (task 004) stays. The existing flicker measurement in `calibration/exposure.py` and `camera-info` stays as a diagnostic. Real recordings made before the lighting fix contain flicker, so judge them on steady (undimmed) frames only.
- Seam angle is in degrees from the travel axis (+x), matching synthetic.py.
- In the decision log, non-finite floats are written as `null` (read back as NaN). Serialise with `contracts.to_log_dict`. Determinism comparisons skip `DETERMINISM_IGNORE_KEYS` and the log_open record.
- A GroupDecision's `t_ref_ns` and `x_lead_in_at_ref` come from the most downstream member (max `belt_x_lead_in`).
- `Frame.t_capture_ns` is the raw driver timestamp on the monotonic clock, not latency-corrected; replay preserves it. The pipeline subtracts camera latency once.
- PartClass values: bolt_025, bolt_0375, nut_025, nut_0375, washer_025, washer_0375 (match synthetic.PART_DIMS; synthetic also has out-of-scope `coin`, `washer_0312`).
- A task MAY change the default VALUES of its own config section (in both `config.py` and `config/default.toml`), with the measurement that justifies it in the task file. It must not add or rename keys, or touch other sections.
- The Scheduler (not the adapter) writes the single `"send"` decision-log record (it knows `miss`). Determinism comparison ignores `t_actual_ns` and compares records per kind, in order within each kind, because send lines interleave from the scheduler thread.
- `PipelineSnapshot` (the GUI view of live state) is owned by task 011 in `pipeline.py`, not contracts.py. Task 014 consumes it.
- `cli.py` is shared by 011, 012, 013, 015, 016: put each subcommand's implementation in its own module and add only a small registration hook in cli.py, to keep merges trivial.
- Board/worktree: new worktrees start from `main`'s commit, so workers must `git checkout -b task/NNN-slug develop` first.

## API contracts: key invariants (full list in contracts-spec.md)
1. Pixels are full-image (x right, y down). Parts travel +x.
2. All times are int ns from the injected `Clock`. Only the pipeline applies camera latency, exactly once.
3. px to inches only via `PlaneMapper`.
4. Belt frame: `belt_x = x_in - v*(t - t0)`. Lead = max x.
5. Gap is along x only, clamped at 0. Link when gap < `rework_gap_in`.
6. A group closes once `rework_gap_in` of empty belt has entered behind it. `flush()` closes all.
7. `Verdict`: CONFIDENT / OUT_OF_SCOPE / UNRESOLVED. MERGED is always UNRESOLVED.
8. Decision table exactly as in the brief. A lone part never goes to REWORK.
9. `t_send = t_ref + d_remaining/v - lead`. A late decision sends MISC with `LATE_DECISION`.
10. Deterministic: same recording gives the same decision log (timings under a separate key).

## Task plan
| Wave | Tasks | Depends on |
| --- | --- | --- |
| 0 | 001 lint baseline | none |
| 1 | 002 contracts + config + fakes; 003 README + flat-field CLI (disjoint) | 001 |
| 2 | 004 segment, 005 seam, 006 track, 007 features+fusion, 008 classify, 009 decide, 010 schedule/output (max 3 at once) | 002 |
| 2b | 002b budget config keys (before the 004 rework and 011) | 002 |
| 3 | 011 pipeline runner + decision log + `sorter run`/replay wiring | 004-010 |
| 4 | 012 grading tool, 013 report, 014 GUI debug overlay, 015 bench + replay regression | 011 |
| 5 | 016 design note + README M2/M3 + `derive-ranges` CLI | 012-015 |

## Status at the end of session 2 (2026-11-17, ~00:40 ET)
The session ended when the account hit its 5-hour usage limit. The agents below stopped mid-task. Agents from session 2 can't be resumed in a new session; dispatch fresh ones.

| Task | Status | Branch / commit | What's left |
| --- | --- | --- | --- |
| 001 lint | DONE, merged | | |
| 002 contracts | DONE, merged (3 review rounds) | | |
| 003 README + flatfield CLI | DONE, merged | | |
| 006 track | DONE, merged | | Optional: public `t0_ns` (carried to 011) |
| 009 decide | DONE, merged | | Optional: frontier arg (carried to 011) |
| 002b budget config keys | TODO (new) | | Adds the 6 ADOPTED budget keys. Blocks the 004 rework and 011. Small; do it first. |
| 004 segment | IN_REVIEW, review NOT done (the reviewer hit the limit and wrote nothing) | `task/004-segment` 060a71c | Fresh Opus review. Must also apply the budget proposal's segment speedups (12-13 ms per frame on the M5 is far over budget) once signed off. |
| 007 features+fusion | IN_REVIEW, review NOT done (same) | `task/007-features-fusion` f5b9eb6 | Fresh Opus review including a profile. 4.5 ms per part per frame on the M5. |
| 010 schedule/output | IN_PROGRESS, review round 1 = CHANGES REQUIRED | `task/010-schedule-output` a7ccaf6 | Make the real-clock jitter test opt-in (`HWSORTER_RUN_JITTER=1`). Optional: try/except around `_fire` in the thread loop; keep LATE_DECISION when an adapter error also occurs. Then review round 2. |
| 005 seam | IN_PROGRESS, partial | `task/005-seam` 5b62bf9 (WIP, UNVERIFIED: only `vision/seam.py`, 534 lines, no tests) | A fresh worker continues from the WIP commit: tests, verification, task file. |
| 008 classify | IN_PROGRESS, partial | `task/008-classify` 79daf97 (WIP, UNVERIFIED: library.py, ranges.py, parts_library.yaml, tests, pyyaml added) | A fresh worker continues: run the tests, finish, verify. Feature names must match 007's branch. |
| 011-016 | TODO | | 011 needs 004-010 DONE. |

Worktrees still on disk under `.claude/worktrees/` (one per open branch above). Remove each with `git worktree remove` after its branch merges. Branches are the source of truth.

**Processing budget: ADOPTED** (the user agreed on 2026-11-17 that ~10 or fewer frames per part is fine; the numbers were left to the orchestrator). See `<REPO_ROOT>/.agent-board/budget-proposal.md`: frame_stride 2, feature_stride 3, max_feature_frames 10, two-stage segmentation. Task 002b adds the keys and must land before the 004 rework and 011. Task 015 guards accuracy (stride 1 vs 2).

## Hardware and camera (session 3 onward)
- **Session 3+ runs on a different machine with NO camera** and no access to this Mac's recordings (`recordings/` is gitignored). Everything must be built and verified with synthetic scenes, generated recordings and unit tests. No task may need the camera, the rig or a real recording to pass.
- **No physical changes are coming yet** (user, 2026-11-17): no collector, no extra light, no moving conveyor.
- Everything that waits on the camera or a physical change is listed on the separate branch `hardware-pending`, in `HARDWARE_PENDING.md`. That includes checkpoint-1 calibration, real-recording checks, lighting, belt motion, serial (M4) and the Dell or Linux camera work (M5). Don't schedule it until the user says the hardware is available. Add newly found hardware-dependent items there (commit on that branch), not to the main task queue.
- Code paths that touch the camera (`camera/avfoundation.py`, `camera/uvc.py`) stay as they are on `develop`. Don't modify them without hardware to test on.

## Gotchas
- Ruff picks up an external rule set (RUF/B/PLW/UP/I) when no repo config exists; task 001 pins it in `pyproject.toml`.
- Camera stalls ~0.2 s on exposure-mode changes; the capture service has an "expect disruption" notice for this.
- Per-frame brightness uses the 75th percentile of the image centre (parts passing would otherwise look like flicker).
- Scale is provisional (~367 px/in); belt speed, lead time, camera latency, runway are "uncalibrated" placeholders.
