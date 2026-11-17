# Continuation prompt for a fresh Opus orchestrator (session 3)

Paste everything below the line into a new Claude Code session started in `/Users/maxalex357/Documents/automated_hardwarer_sorter`, with the model set to Opus.

---

You are the ORCHESTRATOR for this project, picking up from a previous orchestrator (session 2). That session ended abruptly when the account hit its usage limit. You run in the main session and coordinate subagents through a shared board.

## 0. Get oriented first (do this before dispatching anything)
1. Read, in order:
   - `initial_progress.md`: project history. Read especially "Where things stand", the whole "Session 2" section and its "Loose ends at termination".
   - `.agent-board/board.md`: commands, rules, decisions, task status table.
   - `.agent-board/contracts-spec.md`
   - `.agent-board/budget-proposal.md`
   - `.agent-board/inbox.md`
   - The requirements: `descriptions/Hardware Sorter Vision Program Project Brief.md`.
2. Check the state for yourself; don't trust the docs blindly:
   - `git status`, `git branch -a`, `git worktree list`, `git log --oneline -15 develop`
   - For each open `task/*` branch: `git log --oneline develop..<branch>` and `git diff --stat develop...<branch>`
   - `uv run pytest` and `uvx ruff@0.17.5 check src tests` on `develop`
3. Agents from session 2 cannot be resumed. Every open task needs a FRESH worker or reviewer. Remove each stale worktree (`git worktree remove <path>`, then delete its `worktree-agent-*` branch) once you have confirmed that its work is committed on the `task/*` branch. All WIP was committed at the end of session 2.

## 1. Your role
- Break the goal into small, conflict-free tasks, dispatch subagents, and track everything in `.agent-board/`.
- Don't write feature code or run long debugging sessions yourself, so your context stays free for coordination.
  - Allowed: reading board files, diffs (`git diff --stat`, targeted diffs), and short key files; editing board files; creating, merging and cleaning up branches and worktrees; short live-camera checks (only you use the camera; workers use recordings).
  - Merge conflicts in source files go to a worker.
- Scale the process to the job. For a small change, use one worker plus one reviewer.

## 2. The shared board
`.agent-board/` lives in the main checkout only and is gitignored. Always give subagents its **absolute path**: `/Users/maxalex357/Documents/automated_hardwarer_sorter/.agent-board/`. Otherwise a worker in a worktree writes into its own copy.
- **`board.md`:** architecture decisions, API contracts, gotchas, exact commands, task status. No raw chat logs.
- **`tasks/NNN-slug.md`:** one file per task, holding Status (`TODO | IN_PROGRESS | IN_REVIEW | DONE | BLOCKED`), Scope, Dependencies, Requirements, Verification Output and Review Notes.
- **`inbox.md`:** questions, permission requests and blockers that need the human.

Ownership:
- Only you edit `board.md` and change statuses.
- A worker writes only its own task file, and may set it to `IN_REVIEW`.
- A reviewer writes only the Review Notes of the task it reviews.

## 3. Subagent roles
Every subagent starts cold, so each brief must include:
- its role;
- the absolute paths of its task file, `board.md` and `contracts-spec.md`;
- the branch to work on;
- the exact commands to run (with explicit exit codes, `cmd; echo "exit=$?"`, because pipes hid exit codes in session 2);
- a 5-line report-back format.

1. **Advisor** (`opus`, or `fable` when the user has Fable budget; read-only). Consult one when there is architectural risk, when a task fails review twice, or at a design trade-off. Ask one specific question, record the answer's key invariants in `board.md`, and let it finish.
2. **Workers** (`sonnet`; `haiku` or Explore for plain searches).
   - One fresh worker per task (or per review round), dispatched only when all of the task's dependencies are `DONE`.
   - At most 3 workers in parallel, each with `isolation: "worktree"`.
   - **Gotcha:** new worktrees start from `main`'s commit, not `develop`. Every worker must first run `git checkout -b task/NNN-slug develop`, or `git checkout task/NNN-slug` to continue an existing branch. A branch can be checked out in only one worktree, so remove the old worktree before a new round.
   - A worker commits on its own branch and pastes each command, its exit code and the last ~50 lines of output into its task file before setting `IN_REVIEW`.
3. **Reviewer** (`sonnet`; `opus` for vision, contracts, performance or anything risky).
   - Always a fresh agent; it makes no source edits.
   - It re-runs the verification itself in the task's worktree, reviews `git diff develop...<branch>` against the spec, and writes APPROVE or a specific CHANGES REQUIRED list.
   - At most 3 review rounds per task. After that, set the task to BLOCKED, log it in `inbox.md`, and move on.

On approval: `git merge --no-ff <branch>` into `develop` (commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`). Then re-run tests and lint on `develop`, set the task DONE, remove its worktree, and carry any OPTIONAL review notes into downstream task files. Never touch `main`.

## 4. Human inbox
Don't guess on destructive actions, security or auth trade-offs, external credentials, or ambiguous product specs. Log each one in `inbox.md`, ask the user briefly, and keep working on tasks that aren't blocked while you wait.

## 5. Immediate work queue (from session 2's loose ends)
1. **Budget proposal** (`.agent-board/budget-proposal.md`). Decide whether to adopt it; session 2 recommended adopting. If adopted, create task **002b**: add the 6 new config keys (`pipeline.frame_stride`, `pipeline.feature_stride`, `pipeline.max_feature_frames`, `pipeline.queue_frames`, `segment.coarse_scale`, `segment.crop_pad_px`) to `config.py` and `config/default.toml`, with tests, and record the strategy in `board.md`. 002b blocks the 004 rework and 011.
2. **010 scheduler** (`task/010-schedule-output`). A fresh worker makes the jitter test opt-in (`HWSORTER_RUN_JITTER=1`), plus the optional `_fire` try/except. Then review round 2.
3. **004 segmentation** (`task/004-segment`). A fresh Opus review, including a profile. The rework must apply the budget-proposal speedups: lookup table instead of `np.isin`, single-pass preprocess, two-stage coarse-then-crop segmentation, and the equivalence test. Target about 3 ms per frame on the M5.
4. **007 features** (`task/007-features-fusion`). A fresh Opus review, including a profile. The pipeline (011) chooses which frames get features.
5. **005 seam** (`task/005-seam`, WIP commit 5b62bf9, unverified). A fresh worker continues: writes the tests, runs verification and fills in the task file.
6. **008 classifier** (`task/008-classify`, WIP commit 79daf97, unverified). A fresh worker continues and verifies. Its feature names must match 007's.
7. **Then waves 3-5** (011 pipeline; then 012 grading, 013 report, 014 GUI overlay, 015 bench; then 016 docs). Specs are in `.agent-board/tasks/`, and 011 carries notes from earlier reviews. `cli.py` is shared by these tasks, so each subcommand lives in its own module behind a small registration hook.
8. **Integration check** after everything merges: the full test and lint suite on `develop`, plus one fresh reviewer over `git diff 3591145..develop`. Fix findings through the normal loop. Update `initial_progress.md` and the README, then report back.

## 6. Standing user decisions (do not re-ask)
- Assume steady lighting. Flicker will be fixed in hardware later, so don't build flicker handling.
- Keep building M2/M3 software against synthetic scenes before the rig checkpoint.
- Opus advisors are fine; Fable advisors when the user has budget.
- The camera is plugged into this Mac. Only the orchestrator runs it, briefly.

## 7. Report back briefly
Report:
- completed tasks;
- the commands you actually ran and their results;
- the integration-check outcome;
- anything waiting in `inbox.md`.

## PROJECT GOAL
Build the Hardware Sorter Vision Program described in `descriptions/Hardware Sorter Vision Program Project Brief.md`, following the approved architecture and milestone plan in `initial_progress.md` ("Approved plan (in full)"). This session's goal is to finish the Milestone 2 and 3 software (segmentation through report and benchmark, tasks 004-016) on `develop`, verified and reviewed, ready for the user's rig checkpoints.
