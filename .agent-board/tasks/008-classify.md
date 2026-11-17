# 008 parts library + range classifier

Status: DONE (merged into develop, session 3)
Dependencies: 002
Branch: `task/008-classify` (from `develop`)

## Scope (files the task may touch)
config/parts_library.yaml (new), src/hwsorter/classify/*.py, tests/test_classify.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it unless this IS task 002).
Parts library YAML: per part id (PartClass value), display_name, output_code, poses: {pose_name: {feature: [lo, hi]}}. Start from nominal dims in the brief with generous tolerance, kept disjoint for near-miss pairs. Use PyYAML if already a dependency; otherwise add `pyyaml` via `uv add pyyaml` (pyproject.toml + uv.lock changes allowed for this only). `load_library` validates. `RangeClassifier` scores each part+pose by worst-fitting feature (0 at centre, 1 at range edge), applies invariant 7 verdict rules with config.classify thresholds. Also `derive_ranges(examples: list[(PartClass, FusedFeatures)], margin) -> library dict` for later use. Tests: nominal fused vectors classify CONFIDENT correctly for all six; near-miss sizes (e.g. 5/16 nut) OUT_OF_SCOPE or UNRESOLVED, never CONFIDENT wrong class; MERGED -> UNRESOLVED; too few frames -> not CONFIDENT.

## Session note
Session 2 ended (usage limit) mid-task. WIP is committed on `task/008-classify` as 79daf97: library.py, ranges.py, config/parts_library.yaml, tests/test_classify.py, pyyaml added. UNVERIFIED. A fresh worker continues from there.

## Verification Output
(worker pastes each command, exit code, last ~50 lines)

## Review Notes
Session 3: worker verified + fixed a CONFIDENT-wrong case (a 0.688 in out-of-scope washer scored CONFIDENT washer_025) by tightening washer_025/0375 ranges; replaced the WIP's skip-NaN with a NaN-required-feature -> inf -> UNRESOLVED rule; made CONFIDENT require exactly one (part, pose) within the threshold; load_library now rejects unknown feature keys and edge_sharpness as a range key. 128 tests pass (incl. near-miss never-confident, MERGED->UNRESOLVED, few-frames, derive_ranges round trip). Orchestrator reviewed the scorer (ranges.py) against invariants 7/8: correct. library.py hard-codes FEATURE_NAMES because 007 was not in its branch — guard test to reconcile with vision.features.feature_names() is listed in board "Notes for task 011".
