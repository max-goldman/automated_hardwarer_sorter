# 016 design note, README M2/M3, derive-ranges CLI

Status: TODO
Dependencies: 012, 013, 014, 015
Branch: `task/016-docs-derive` (from `develop`)

## Scope (files the task may touch)
README.md, docs/design.md (new), src/hwsorter/cli.py (add `derive-ranges` only), tests/test_derive.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it or config.py; request changes via the task file).
`sorter derive-ranges <graded sessions...> [--margin] [--out config/parts_library.yaml]` using classify.derive_ranges; never overwrites hand edits silently (writes to a new file unless --in-place). docs/design.md: short design note (approach, features that separate each part pair incl. 1/4 washer vs 3/8 nut, steps to add a new part, seam handling, timing model, known limits). README: fill in the M2/M3 section (run, replay with pipeline, grade, report, bench, derive-ranges) with exact commands verified by running each `--help`.

## Verification Output
(worker pastes each command, exit code, last ~50 lines)

## Review Notes
(reviewer only)
