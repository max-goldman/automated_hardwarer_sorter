# 013 accuracy and latency report

Status: TODO
Dependencies: 011, 012 (label format)
Branch: `task/013-report` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/report/*.py (new), src/hwsorter/cli.py (add `report` subcommand only), tests/test_report.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it or config.py; request changes via the task file).
Per the brief's report section: accuracy, contamination, misc and rework rates, confusion matrix (6 classes + out of scope vs 8 outputs), per-class precision/recall, on-seam accuracy broken out, latency p50/p95/p99/max per stage, fps, drops, deadline misses, send-time error; pass/fail per Performance requirement; export CSV + HTML (self-contained, no external assets). `sorter report <session>... [--rerun]` where --rerun re-runs graded recordings through the current pipeline (fast path: re-classify stored fused features only). Tests on hand-built decision logs + labels with known answers (including a contamination case and a parts-fed miss).

## Verification Output
(worker pastes each command, exit code, last ~50 lines)

## Review Notes
(reviewer only)
