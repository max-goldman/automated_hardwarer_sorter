# 014 live debug view overlay

Status: TODO
Dependencies: 011
Branch: `task/014-gui-debug` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/gui/*.py except grading.py, tests/test_gui.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it or config.py; request changes via the task file).
Add the pipeline debug overlay to the existing Qt live view: outlines, track IDs, predicted output + confidence, key measurements, gap to neighbours and Rework flag, countdown to each scheduled send, per-part latency, fps and drops. Toggleable; draws from PipelineSnapshot at <= 30 Hz on the GUI thread only; must not slow the pipeline (measure fps with overlay on vs off on a synthetic run and report). `sorter gui --pipeline` (or a toggle in the app) enables it. Offscreen tests.

## Verification Output
(worker pastes each command, exit code, last ~50 lines)

## Review Notes
(reviewer only)
