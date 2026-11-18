# 025 make `sorter run` visible: live window, status line, optional recording

Status: DONE (merged)
Dependencies: 020 merged. Parallel with 023 (vision) and 024 (5/16 rename).
Branch: `task/025-run-visibility` (from `develop`)

## Why
The user ran `sorter run --source camera --stationary --headless` at the rig and saw only the "decision log: ..." line. Run 1 (150 s) made no decisions and there is no way to tell why (likely a large shadowed edge region classified as a hand, holding all decisions); run 2 decided 3 parts but the user did not notice. The live GUI overlay exists (gui/debug_overlay.py, task 014) but `sorter run` never opens a window ("the live GUI overlay is task 014; running headless").

## Scope
`src/hwsorter/cli.py` (`run` only: flags + wiring), `src/hwsorter/gui/` (a run window reusing `video.py` + `DebugOverlay`; small overlay additions), `src/hwsorter/pipeline.py` (only if a status accessor is missing from `PipelineSnapshot`), `src/hwsorter/session.py` (only to allow recording alongside the pipeline if not already possible), tests (offscreen Qt), README (run section). Do not touch vision/, classify/, decide/, contracts.py, synthetic.py PART_DIMS, or config sections other than adding keys you need under `[pipeline]`/`[gui]`.

## Requirements
1. **Status line** (headless and windowed): about once per second print one line, e.g. `t=12.3s  in view: 2 parts (1 measured, 1 measuring 6/10 frames)  hand: no  fps 120  last: group 3 [#5] -> nut_025 (lone_confident)`. Stationary: show measuring progress and "HAND in view: holding decisions" (and why: the blob's area in in² and that it touches the edge, so a false hand is diagnosable). Moving mode: parts in view, pending sends with countdown, late count. Use `\r`-free plain lines so it works in any terminal. `--quiet` disables it. DECISION lines stay as they are.
2. **Live window**: `sorter run` WITHOUT `--headless` opens a Qt window showing the live video with the DebugOverlay (outlines, ids, class/verdict, group output, hand indicator in stationary mode, HUD) for camera, recordings and synthetic. Closing the window ends the run cleanly (session_end written). The pipeline core stays Qt-free; the window only consumes `PipelineSnapshot`.
3. **Clean shutdown**: Ctrl-C (once) in the terminal ends the run cleanly with session_end written, in both modes. Verify with the camera path's code reading (no camera available to you) and with recordings/synthetic in tests.
4. **Record while running**: `sorter run --record NAME` also writes a lossless recording (existing recorder) of the same frames, so a live session can be replayed with `sorter run --source NAME --stationary`. The decision log's session_start should reference the recording path.
5. Tests offscreen: the status formatter (stationary and moving, incl. hand diagnostics), the window opens/updates/closes on a short synthetic source and writes session_end, `--record` produces a readable recording whose replay gives the same stationary decisions.

## Verification
pytest fully green; ruff check + format --check exit 0; `sorter run --source synthetic --stationary --headless --fast` shows status lines; `sorter run --source synthetic --stationary --seconds 3` with `QT_QPA_PLATFORM=offscreen` exits 0 with session_end.

## Verification Output
Round 1 worker 8aaa88e: pytest green, ruff/format exit=0, both synthetic runs exit=0, session_end written once.

## Review Notes
Round 1 (fresh Sonnet): CHANGES REQUIRED: `_run_live` creates the camera Session before the try, so a build_pipeline error leaks the camera and the log. Orchestrator also required: restore the SIGINT handler, clean Ctrl-C during session.start(), last_decision not from EventLog.recent(), print the recording summary.

Round 2 worker 8bb23c7: all 5 items addressed (try/finally around Session+build, SIGINT restore, Ctrl-C in start-up, last_decision from the pipeline, recording summary); pytest exit=0, ruff/format exit=0, both synthetic runs exit=0. Round 2 review: orchestrator (focused diff, +118/-49) — sound. Merged.
