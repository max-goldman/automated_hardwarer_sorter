# Handoff: session 4 → session 5 (fresh Opus orchestrator, another machine)

Session 4 (2026-11-17) ran on the rig Mac (Apple M5, camera connected, stationary belt) and ended when the user ran out of usage. Paste the prompt at the bottom into a new Claude Code session (Opus) started in the repo root.

## 0. Setup on the new machine
1. Clone and fetch every branch, then check out `develop`:
   ```
   git clone https://github.com/max-goldman/automated_hardwarer_sorter.git && cd automated_hardwarer_sorter
   git fetch --all
   for b in $(git branch -r | grep -E 'origin/(develop|task/|hardware-pending)'); do git branch --track "${b#origin/}" "$b" 2>/dev/null; done
   git checkout develop && uv sync && uv run pytest -q; echo "exit=$?"
   ```
   `develop` should be fully green (4-5 skipped, 1 accepted xfail), with ruff check and format clean.
2. **Real data is NOT in git.** The recordings (`recordings/`, ~2 GB) and the rig calibration (`config/calibration/bananabook-pro-m5.json` + `.flatfield.npy`) exist only on the rig Mac. Tasks 028 and 029 are verified against them. If the user copies them over (e.g. via a cloud drive), put them at the same paths and set `HWSORTER_MACHINE=bananabook-pro-m5` so the calibration file is found. Copy at least these recordings:
   - `*-still-{bolt025,bolt0375,nut025,nut0375,washer025,washer0375}` (~300 MB). One part each, still. The `0375` names are really the **5/16** parts.
   - `*-rig1` (384 MB): the first live trial.
   - `*-0-25{bolt1,nut1,washer1}` (~610 MB): 1/4 parts pushed back and forth on purpose to imitate motion.
   - `*-empty-belt-still` (56 MB): the source of the current flat-field.
   - Do NOT copy `*-scratch029-*`, which is worker test output (delete it on the Mac).

   Without the recordings, work only from synthetic scenes and say so.
3. Also copy `config/local.toml` ONLY if the new machine drives the rig camera. It sets `[camera] exposure_ms = 8.0`. Tests ignore local config (task 022).

## 1. Read, in order
- `.agent-board/board.md`: commands, rules, decisions. Read the "Session 4" table and "Rig facts" sections first.
- `.agent-board/inbox.md`.
- `.agent-board/tasks/028-*.md` and `029-*.md`: the two open tasks, with full evidence.
- `docs/design.md`, `README.md`.
- `initial_progress.md`. Its "Where things stand" section covers sessions 1-3. This file covers session 4.
- `descriptions/Hardware Sorter Vision Program Project Brief.md`. It was updated in session 4: second size 5/16-18, nyloc nuts, camera height 7.25 in.

## 2. What session 4 did (all merged into `develop`, each reviewed)
| Task | Result |
| --- | --- |
| 019 `sorter measure` | Per-part real-image diagnostic: feature table vs library ranges, overlay.png/mask.png/measure.json; skips dimmed frames |
| 021 cross-machine compare | `replay --compare` compares decisions exactly and floats within 1e-4 (`--strict` for byte-identical). This fixed 5 golden tests that failed on the M5 |
| 022 test isolation | Tests run on a temp root without `config/local.toml` or the calibration |
| 020 stationary mode | `sorter run --stationary` (`belt.mode`): decide parts once settled, hand hold, re-decide on move/removal, `supersedes`, no sends. Took 3 review rounds (swap-before-measure, fast swap) |
| 023 real-image robustness | Per-frame illumination surface, attached-shadow trim, outline snap to the outer step, hull circularity, crest shank width, hole kind relative to the local belt, seam residue drop (also fixed the seam ghost track). Real 1/4 parts became CONFIDENT; washer OD within 0.0002 in of the calipers |
| 024 second size = 5/16-18 | User decision (the brief's 3/8 was an error). PartClass `*_0375` became `*_0312`; nyloc nuts; gapped ranges; `tests/test_library_safety.py` (no wrong CONFIDENT within ±7% scale). **All six real still recordings are CONFIDENT and correct** |
| 025 run visibility | `sorter run` opens a live window (overlay + HUD), status line every second with hand diagnosis, clean Ctrl-C, `--record NAME`, `--quiet` |

Rig facts:
- Exposure 8 ms. It spans the lights' ~4 ms PWM period, so 0% of frames are dimmed. It is too much blur for a moving belt.
- Scale 387.7 px/in.
- Camera: lens ~7.25 in and sensor ~8 in above the belt. Thick parts read +3-5% from parallax.
- Ambient light drifts (now handled in software).
- The lights were turned up late in the session (belt 238 vs the 202 flat-field). Rebuild the flat-field once the lighting is final.

## 3. Open work (in priority order)
1. **029 stationary live fixes** (branch `task/029-stationary-live-fixes`, WIP commit `d32957f`, NOT verified or reviewed; the worker's stop report is at the end of its task file). The first live trial (`rig1`) showed three problems:
   - a still bolt's track churned and was re-measured;
   - fingers at the image edge became parts;
   - the window+record run had 656 queue overflows.

   The worker added an identity chain, an edge complex, a queue-side stride and centroid-aligned reappear checks; this also absorbs 026. rig1 replay already gives exactly one bolt_025 decision and no finger parts. Next: a fresh Opus worker checks out the branch, works through the task file's "To do" list (full pytest is the first unknown), then a fresh Opus review.
2. **028 bolt head at any angle / trim facet protection / moving nut** (branch `task/028-segment-followups`, WIP commit `56af8c8`, NOT verified or reviewed).
   - Evidence: on `0-25bolt1` the bolt (20-35° from travel) reads head_width 0.40 (true ≥0.43), so 263/321 frames come out OUT_OF_SCOPE. The moving nut fails hole_kind/circularity in about half its frames.
   - The worker has committed: the trim seeds only from open belt (keeps edge-enclosed faces), and the bolt profile is measured along the shank axis.
   - Moving-bolt CONFIDENT frames went 40→129 with no wrong-class CONFIDENT. The moving nut's root cause is the nyloc insert facing up (a bright bore), not blur. `hole_dark_threshold` changed (0.30→0.45), so the goldens must be regenerated.
   - The diagnostic scripts are committed in `.agent-board/tools/028/` (they need the real recordings).
   - Next: a fresh Opus worker works through the task file's "To do" list, then a fresh Opus review. Merge 029 first; the two branches touch different files.
   - Watch: the moving nut's circularity (~0.965) sits at the nut range's upper edge, and the 5/16 bolt head range starts at 0.52 while the 1/4 bolt reads up to 0.514. Either may need a library decision tested with tests/test_library_safety.py.
3. **Queued, not started:**
   - **030** `sorter record --frames N --every K`: the user wants small captures, because recordings use ~25 MB/s. Do it after 029 merges, since both touch cli/session.
   - **027** height/parallax correction (calibration level).
   - The CLI 30-part synthetic run sends group 28 (a washer on the seam, seen for 2 frames) to misc. This is a pre-existing seam issue.
   - Open design question for the user: stacked or touching parts that never separate count as ONE part, so they go to MISC instead of REWORK. That is safe, but it is a miss.

## 4. Process (unchanged; see board.md)
- You orchestrate and don't write feature code.
- Workers are Sonnet, or Opus for vision or tracker work, each in a worktree (`isolation: "worktree"`); they report verification in their final message, and you write the board.
- A fresh reviewer re-runs the verification. Use Opus for anything that can cause contamination.
- At most 3 review rounds per task; merge `--no-ff` into `develop`; never touch `main`.
- Exit codes come from `cmd; echo "exit=$?"`, never through `| tail`.
- Commit board edits before merges.
- The usage limit has killed agents twice. Tell workers to commit WIP early and often.
- Standing user decisions:
  - assume steady lighting;
  - process every 2nd frame and use ≤10 feature frames per part;
  - the second size is 5/16-18 and the nuts are nyloc;
  - contamination safety is paramount (an unresolved part goes to MISC, never to a wrong bin).

---

## Prompt to paste (new Opus session, repo root)

You are the ORCHESTRATOR for the Hardware Sorter Vision Program, picking up from session 4 on a different machine. First read `handoff.md` and follow its section 0 (setup and real-data availability) and section 1 (reading order). Then resume the open work in section 3 in priority order:
1. Finish task 029 from its WIP branch with a fresh worker, then a fresh Opus review.
2. Then task 028.
3. Then task 030.

Follow the process in section 4 and `.agent-board/board.md`. Before dispatching, check which real recordings exist on this machine. If they are missing, ask the user whether they can copy them; meanwhile verify on synthetic scenes only and say so. Report back briefly after each merge: what changed, the commands run and their results, and anything waiting in `.agent-board/inbox.md`.
