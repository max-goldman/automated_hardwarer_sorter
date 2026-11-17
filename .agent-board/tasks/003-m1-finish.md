# 003 README + flat-field CLI

Status: DONE
Dependencies: 001
Branch: `task/003-m1-finish` (from `develop`)

## Scope (files the task may touch)
README.md, src/hwsorter/cli.py, tests/test_cli.py (new)

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it unless this IS task 002).
Finish milestone 1 software:
- Add `sorter calibrate flatfield <recording>`: builds the illumination map from an empty-belt recording using the existing function in `calibration/flatfield.py` and saves it through `calibration/store.py` (look at how other calibrate subcommands save). Test it on a short synthetic recording written via the existing recorder.
- Write README.md: what it is; setup on macOS, Linux, Windows (`scripts/setup.sh` on mac/linux; Windows: install uv then `uv sync`); every CLI command with an example (run `uv run sorter --help` and each subcommand's `--help` to get them right); config layering (default.toml, local.toml); where calibration and recordings live; how to run tests and lint (commands from board.md). Mark the Linux camera backend as milestone 5 (replay works everywhere). Leave a clearly marked section for M2/M3 commands to be filled later.

## Verification Output
Commit: 355e326 on task/003-m1-finish
Worktree: <REPO_ROOT>/.claude/worktrees/agent-a4da93aa0428a9671
Note: `calibrate` is now nested subcommands (target/show/flatfield). Flat-field saved as config/calibration/<machine>.flatfield.npy and recorded in Calibration.flatfield_file.

```
$ uv run pytest
...................................                                      [100%]
35 passed in 11.34s
exit 0
$ uvx ruff@0.17.5 check src tests
All checks passed!
exit 0
$ uvx ruff@0.17.5 format --check src tests
32 files already formatted
exit 0
$ uv run sorter --help
usage: sorter [-h] [--config CONFIG] [-v]
              {camera-info,gui,record,replay,recordings,calibrate} ...

Hardware sorter vision program

positional arguments:
  {camera-info,gui,record,replay,recordings,calibrate}
    camera-info         list camera modes, then measure frame rate, drops,
                        brightness and flicker
    gui                 live view and calibration tools
    record              record raw frames to disk (lossless)
    replay              read a recording back and verify it
    recordings          list recordings
    calibrate           calibration helpers (the interactive tools are in the
                        GUI)

options:
  -h, --help            show this help message and exit
  --config CONFIG       extra config file applied over config/default.toml and
                        config/local.toml
  -v, --verbose
exit 0
$ uv run sorter calibrate flatfield --help
usage: sorter calibrate flatfield [-h] [--max-frames MAX_FRAMES] recording

positional arguments:
  recording             recording name or path (empty, moving belt)

options:
  -h, --help            show this help message and exit
  --max-frames MAX_FRAMES
                        frames used for the median
exit 0
```

## Review Notes
Reviewer re-run in worktree (commit 355e326):
- `uv run pytest -q`: exit 0 (35 passed)
- `uvx ruff@0.17.5 check src tests`: exit 0
- `uvx ruff@0.17.5 format --check src tests`: exit 0
- `uv run sorter --help`, `calibrate flatfield --help`, `calibrate target --help`, `calibrate show --help`: all exit 0 (also checked camera-info/gui/record/replay/recordings --help: exit 0)
- Scope: only README.md, src/hwsorter/cli.py, tests/test_cli.py changed. OK.

Checked OK: flatfield reuses build_flatfield and save_calibration; missing recording gives a clear one-line error (`error: not a recording (no meta.json): ...`, exit 1); empty recording raises "has no frames"; every documented flag matches --help; no invented flags; config layering, HWSORTER_ROOT/MACHINE, tests/lint commands (match board.md), M5 Linux caveat, M2/M3 placeholder, macOS/Linux/Windows setup all present and accurate. Restructure to nested subparsers keeps `calibrate target [--out]` and `calibrate show` working; only `calibrate --out X target` (option before action) no longer parses, negligible. Test is meaningful (ramp image, round trip through load_calibration, shape and value check). Note: nothing in src reads `Calibration.flatfield_file` yet, so "loaded back" is only tested by the test itself; acceptable for M1, a consumer is a later task.

CHANGES REQUIRED:
1. README.md (Examples block and "Typical calibration workflow" step 3): `sorter replay empty-belt`, `sorter gui --source my-recording`, `sorter calibrate flatfield empty-belt` will fail. `record` names folders `<YYYYmmdd-HHMMSS>-<name>` (session.py start_recording), and replay/gui/flatfield resolve the argument literally under recordings/ (no prefix match). Verified: `calibrate flatfield nope` errors on recordings/nope. Fix the docs: say to copy the folder name printed by `sorter record` / listed by `sorter recordings`, and use a placeholder like `20260101-120000-empty-belt` in the examples (or add prefix resolution, but that is out of this task's stated scope).
2. src/hwsorter/cli.py `--max-frames` (flatfield parser, ~line 290): `--max-frames 0` hits ZeroDivisionError (`len(reader) // args.max_frames`), shown as an obscure `error: integer division...`. Minor; validate `>= 1` with a clear message.
3. README.md step 4: "`calibrate show` lists what is calibrated" but `show` does not print flatfield_file (it iterates scale, roi, focus, exposure, belt only), so the user gets no confirmation of the flat-field there. Either reword or add a flatfield line to `show` (cli.py ~line 220).

## Verification Output - Round 2
Commit: 7d0e55b on task/003-m1-finish (on top of 355e326)
Worktree: <REPO_ROOT>/.claude/worktrees/agent-a340b9bf1ce6d2690

Board commands (run after final ruff format):
```
$ uv run pytest
.....................................                                    [100%]
37 passed
exit 0
$ uvx ruff@0.17.5 check src tests
All checks passed!
exit 0
$ uvx ruff@0.17.5 format --check src tests
32 files already formatted
exit 0
$ uv run sorter --help
(usage; commands camera-info,gui,record,replay,recordings,calibrate)
exit 0
```

Review items:
1. Short recording names: new `resolve_recording(cfg, spec)` in cli.py (existing path, then exact folder under recordings dir, then newest `*-<spec>`; else FileNotFoundError listing available). Used by replay, calibrate flatfield, and gui --source (resolved in cli before calling gui run, since session.py is out of scope). README examples now valid; README explains the rule. Test: test_resolve_recording (newest wins, exact name, path, error lists available).
2. `--max-frames` uses an argparse type rejecting < 1 (`error: argument --max-frames: must be at least 1`, exit 2). Test: test_max_frames_must_be_positive.
3. `calibrate show` now prints a flatfield line (path and [ok]/[MISSING], or "not calibrated"); README step 4 reworded.

README examples run in a temp root (HWSORTER_ROOT, synthetic recording), all as written with short name `empty-belt`:
- `record empty-belt --source synthetic --seconds 2 --notes x`: exit 0 (236 frames)
- `recordings`: exit 0
- `replay empty-belt`: exit 0 (236 frames read)
- `calibrate target --out ...pdf`: exit 0
- `calibrate show` before: exit 0, "flatfield not calibrated"
- `calibrate flatfield empty-belt --max-frames 80`: exit 0 (118 frames, saved .npy)
- `calibrate show` after: exit 0, "flatfield .../bench.flatfield.npy [ok]"
- `calibrate flatfield nope`: exit 1, "no recording matches 'nope' ... (available: 20261117-000555-empty-belt)"
- `calibrate flatfield empty-belt --max-frames 0`: exit 2, clear message
Not run (need camera or display): `camera-info`, `gui` (gui --source uses the same helper, which is unit-tested).

### Review Notes - Round 2
Reviewer re-run in worktree (commit 7d0e55b): `uv run pytest` exit 0 (37 passed); `uvx ruff@0.17.5 check src tests` exit 0; `ruff format --check` exit 0; `sorter --help` exit 0; `sorter calibrate flatfield --help` exit 0. Scope: only README.md, cli.py, test_cli.py. OK.

Round-1 items verified fixed: (1) resolve_recording works; ran in a temp HWSORTER_ROOT: `record empty-belt --source synthetic`, `replay empty-belt`, `calibrate flatfield empty-belt --max-frames 20`, `calibrate show` (flatfield line with [ok]) all exit 0; `calibrate flatfield nope` exit 1 with a one-line error listing available recordings. (2) `--max-frames 0` exit 2 with "must be at least 1", test present. (3) `calibrate show` prints a flatfield line; README reworded.
Diff review: newest-by-name is correct (timestamp prefix sorts lexically), requires meta.json for suffix matches; no real defects. Nits only (not blocking): a cwd directory named like the short name takes precedence over recordings/; `gui --source` literals "camera"/"synthetic" shadow recordings of those names.

APPROVE
