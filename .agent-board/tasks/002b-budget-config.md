# 002b budget config keys

Status: TODO
Dependencies: none (002 DONE)
Branch: `task/002b-budget-config` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/config.py, config/default.toml, tests/test_contracts.py (or a new tests/test_config_budget.py)

## Requirements
Read `<REPO_ROOT>/.agent-board/budget-proposal.md` (ADOPTED) and `<REPO_ROOT>/.agent-board/board.md`.
Add, with doc comments, pydantic validation and matching TOML defaults:
- `pipeline.frame_stride: int = 2` (ge=1; process a frame only when seq % frame_stride == 0)
- `pipeline.feature_stride: int = 3` (ge=1; counted in processed frames)
- `pipeline.max_feature_frames: int = 10` (ge=1)
- `pipeline.queue_frames: int = 120` (ge=1)
- `segment.coarse_scale: int = 2` (1 or 2; 1 = the old full-frame path)
- `segment.crop_pad_px: int = 16` (ge=12)
Add a cross-field check that `max_feature_frames >= classify.min_frames` (and `>= features` min valid frames, if that key exists). Add tests for the defaults, bounds and the cross-field check. Unknown keys must still be rejected. In `config.py`, document that track frame counts (`max_missed_frames`, `settle_frames`, `min_frames`) count PROCESSED frames.

## Verification Output

## Review Notes
