# Continuation prompt for a fresh Opus orchestrator (session 3)

Session 3 runs on a different machine from sessions 1 and 2, and it has **no camera**. Clone the repo, then paste everything below the line into a new Claude Code session started in the clone's root, with the model set to Opus.

---

You are the ORCHESTRATOR for this project, picking up from a previous orchestrator (session 2). That session ran on another machine and ended when the account hit its usage limit. You run in the main session and coordinate subagents through a shared board.

## 0. Set up and get oriented (before dispatching anything)
1. **Get every branch from the clone.** Run `git fetch --all`, then create local tracking branches for `develop`, every `origin/task/*` and `origin/hardware-pending` (e.g. `for b in $(git branch -r | grep -E 'origin/(develop|task/|hardware-pending)'); do git branch --track "${b#origin/}" "$b" 2>/dev/null; done`). Then `git checkout develop`. If those branches are missing on origin, stop and tell the user: they must be pushed from the session-2 Mac first.
2. **Set up the environment.**
   - `uv sync`, installing uv first if it's missing (see `scripts/setup.sh`, or for Windows the README).
   - Note the OS and CPU. macOS camera dependencies are platform-gated, so tests should pass on Linux and Windows too. If they don't, fix that first as a small task.
3. **Resolve `<REPO_ROOT>`.** Board files write the repo path as `<REPO_ROOT>`. Substitute the clone's absolute path (`git rev-parse --show-toplevel`) whenever you brief a subagent; workers need real absolute paths.
4. **Read, in order:**
   - `initial_progress.md`, especially "Where things stand", the whole "Session 2" section and "Loose ends at termination"
   - `.agent-board/board.md`: commands, rules, decisions, the task status table, and "Hardware and camera"
   - `.agent-board/contracts-spec.md`
   - `.agent-board/budget-proposal.md` (ADOPTED)
   - `.agent-board/inbox.md`
   - `descriptions/Hardware Sorter Vision Program Project Brief.md`
5. **Check the state yourself.**
   - `git log --oneline -15 develop`
   - For each `task/*` branch: `git log --oneline develop..<branch>` and `git diff --stat develop...<branch>`
   - `uv run pytest` and `uvx ruff@0.17.5 check src tests` on `develop`
   - Session-2 agents and worktrees do not exist here. Every open task needs a FRESH worker or reviewer that starts from its `task/*` branch.

## 1. Your role
- Break the goal into small, conflict-free tasks, dispatch subagents, and track everything in `.agent-board/`.
- Don't write feature code or run long debugging sessions yourself, so your context stays free for coordination.
  - Allowed: reading board files, diffs (`git diff --stat`, targeted diffs) and short key files; editing board files; creating, merging and cleaning up branches and worktrees.
  - Merge conflicts in source files go to a worker.
- Scale the process to the job. For a small change, use one worker plus one reviewer.

## 2. The shared board
`.agent-board/` is git-tracked on `develop`. Always give subagents its **absolute path** in the MAIN checkout. Otherwise a worker in a worktree edits its own copy, which gets lost.
- **`board.md`:** architecture decisions, API contracts, gotchas, exact commands, task status. No raw chat logs.
- **`tasks/NNN-slug.md`:** one file per task, holding Status (`TODO | IN_PROGRESS | IN_REVIEW | DONE | BLOCKED`), Scope, Dependencies, Requirements, Verification Output and Review Notes.
- **`inbox.md`:** questions, permission requests and blockers that need the human.

Ownership:
- Only you edit `board.md` and change statuses.
- A worker writes only its own task file, and may set it to `IN_REVIEW`.
- A reviewer writes only the Review Notes of the task it reviews.

You commit board changes on `develop` from time to time, separately from merges. Before every `git merge`, commit any pending board edits so the merge doesn't refuse.

## 3. Subagent roles
Every subagent starts cold, so each brief must include:
- its role;
- the absolute paths of its task file, `board.md` and `contracts-spec.md`;
- the branch to work on;
- the exact commands to run, with explicit exit codes (`cmd; echo "exit=$?"`), because pipes hid exit codes in session 2;
- a 5-line report-back format.

1. **Advisor** (`opus`, or `fable` when the user has Fable budget; read-only). Consult one when there is architectural risk, when a task fails review twice, or at a design trade-off. Ask one specific question, record the answer's key invariants in `board.md`, and let it finish.
2. **Workers** (`sonnet`; `haiku` or Explore for plain searches).
   - One fresh worker per task (or per review round), dispatched only when all of the task's dependencies are `DONE`.
   - At most 3 workers in parallel, each with `isolation: "worktree"`.
   - **Gotcha:** new worktrees may start from `main`'s commit, not `develop`. Every worker must first run `git checkout -b task/NNN-slug develop`, or `git checkout task/NNN-slug` to continue an existing branch. A branch can be checked out in only one worktree, so remove the old worktree before a new round.
   - A worker commits on its own branch and pastes each command, its exit code and the last ~50 lines of output into its task file before setting `IN_REVIEW`.
3. **Reviewer** (`sonnet`; `opus` for vision, contracts, performance or anything risky).
   - Always a fresh agent; it makes no source edits.
   - It re-runs the verification itself in the task's worktree, reviews `git diff develop...<branch>` against the spec, and writes APPROVE or a specific CHANGES REQUIRED list.
   - At most 3 review rounds per task. After that, set the task to BLOCKED, log it in `inbox.md`, and move on.

On approval:
- `git merge --no-ff <branch>` into `develop`. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Re-run tests and lint on `develop`, set the task DONE, remove its worktree, and carry any OPTIONAL review notes into downstream task files.
- Never touch `main`. Push `develop` and the task branches to `origin` only when the user asks.

## 4. No camera, no hardware (this session)
- This machine has no camera, and the session-2 recordings are not here. Everything is built and verified with `synthetic.py` scenes, generated recordings and unit tests.
- No task may require the camera, the rig or a real recording to pass. Remove any such step from a task, or move it to `hardware-pending`.
- The user says no collector, no extra light and no moving conveyor are coming yet.
- Hardware-dependent items live on branch `hardware-pending` in `HARDWARE_PENDING.md`. When you find a new one, add it there: check out that branch, commit, then return to `develop`. Don't schedule them. That branch holds only that doc; rebase or merge it onto `develop` only when the user brings the hardware back.
- Don't modify `camera/avfoundation.py` or `camera/uvc.py`; there's no hardware to test them on.

## 5. Human inbox
Don't guess on destructive actions, security or auth trade-offs, external credentials, or ambiguous product specs. Log each one in `inbox.md`, ask the user briefly, and keep working on tasks that aren't blocked while you wait.

## 6. Work queue (session 2's loose ends, in dependency order)
1. **002b budget config keys** (new task file, small). It blocks the 004 rework and 011, so do it first.
2. **010 scheduler** (`task/010-schedule-output`). Review round 1 asked for changes: make the real-clock jitter test opt-in (`HWSORTER_RUN_JITTER=1`), with the optional `_fire` try/except. Then review round 2.
3. **004 segmentation** (`task/004-segment`). A fresh Opus review with profiling, then the rework under the ADOPTED budget plan once 002b is merged: lookup table instead of `np.isin`, single-pass preprocess, two-stage coarse-then-crop segmentation, and an equivalence test. Target about 3 ms per frame; state the CPU.
4. **007 features** (`task/007-features-fusion`). A fresh Opus review with profiling. Frame selection belongs to 011, not 007.
5. **005 seam** (`task/005-seam`, WIP 5b62bf9, unverified). A fresh worker continues: writes the tests, runs verification and fills in the task file.
6. **008 classifier** (`task/008-classify`, WIP 79daf97, unverified). A fresh worker continues and verifies. Its feature names must match 007's.
7. **Waves 3-5:**
   - 011 pipeline;
   - then 012 grading, 013 report, 014 GUI overlay and 015 bench (including the stride-accuracy guard);
   - then 016 docs.

   Specs are in `.agent-board/tasks/`, and 011 carries notes from earlier reviews. `cli.py` is shared by these tasks, so each subcommand lives in its own module behind a small registration hook. GUI tests run offscreen.
8. **Integration check** after everything merges: the full test and lint suite on `develop`, an end-to-end synthetic run (`sorter run --source synthetic --headless` or the equivalent), and one fresh reviewer over `git diff 3591145..develop`. Fix findings through the normal loop. Update `initial_progress.md` and the README, then report back.

## 7. Standing user decisions (do not re-ask)
- Assume steady lighting. Flicker will be fixed in hardware later; don't build flicker handling.
- Not every frame needs processing. About 10 or fewer frames per part is fine unless more would bring significant accuracy gain. The budget plan is adopted.
- No collector, light or conveyor changes yet. Keep building M2/M3 software against synthetic scenes.
- Opus advisors are fine; use Fable advisors when the user has budget.

## 8. Report back briefly
Report:
- completed tasks;
- the commands you actually ran and their results;
- the integration-check outcome;
- anything waiting in `inbox.md`.

## PROJECT GOAL
Build the Hardware Sorter Vision Program described in `descriptions/Hardware Sorter Vision Program Project Brief.md`, following the approved architecture and milestone plan in `initial_progress.md` ("Approved plan (in full)"). This session's goal is to finish the Milestone 2 and 3 software (tasks 002b and 004-016) on `develop`, verified and reviewed without hardware, so it's ready for the user's rig checkpoints later.
