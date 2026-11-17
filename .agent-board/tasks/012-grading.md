# 012 grading tool

Status: TODO
Dependencies: 011
Branch: `task/012-grading` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/grading/*.py (new), src/hwsorter/gui/grading.py (new), src/hwsorter/cli.py (add `grade` subcommand only), tests/test_grading.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it or config.py; request changes via the task file).
Per the brief's Grading tool section plus the plan's two additions ("several parts in this crop" label, "parts fed" count per run). Data layer (no Qt): load a session's decision log + crops; label store (JSON lines, saved on every keystroke, resumable, allows going back); labels anchored to (t_ref_ns, belt position) not track id, with rematch-by-overlap after pipeline re-runs; compute each part's correct output with `decide.rules.correct_output` from labels + measured grouping. Qt UI: step through parts, show crop + full frame, predicted identity + confidence + group output, one key per class, one for out of scope, one to exclude, one for several-parts; default = predicted (Enter confirms). `sorter grade <session>` works on a live session folder or one produced by replay. Tests: data layer fully; Qt UI driven offscreen like tests/test_gui.py.

## Verification Output
(worker pastes each command, exit code, last ~50 lines)

## Review Notes
(reviewer only)
