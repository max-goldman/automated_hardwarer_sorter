# 005 seam detection and handling

Status: IN_PROGRESS
Dependencies: 002
Branch: `task/005-seam` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/vision/seam.py, tests/test_seam.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it unless this IS task 002).
Implement `SeamHandler` per contracts-spec.md. Seam = diagonal band spanning the belt width at a fixed angle and width (config seam.*). Detect by geometry from blobs/Prepared; it never becomes a part. Learn a seam template from part-free passes; when a part overlaps the seam, subtract the registered template to recover the part outline and set `on_seam=True`. Track seam passes to estimate belt speed (`belt_speed_in_s`) from successive frames. Use synthetic.py's seam rendering for tests: empty seam pass yields zero blobs; a part on the seam yields one blob flagged on_seam with area within 10% of nominal; belt speed estimate within 2% of the synthetic speed.

## Session note
Session 2 ended (usage limit) mid-task. WIP is committed on `task/005-seam` as 5b62bf9: `vision/seam.py` only (534 lines), UNVERIFIED, no tests. A fresh worker continues from there.

## Verification Output
(worker pastes each command, exit code, last ~50 lines)

## Review Notes
(reviewer only)
