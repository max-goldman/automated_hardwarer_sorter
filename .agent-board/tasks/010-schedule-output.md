# 010 scheduler + output adapters

Status: IN_PROGRESS
Dependencies: 002
Branch: `task/010-schedule-output` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/schedule/*.py, src/hwsorter/output/*.py, tests/test_schedule.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it unless this IS task 002).
Implement `send_time_ns`, `Scheduler` (own thread, priority queue by t_send, sleep then spin last scheduler.spin_ms using the injected Clock; works with ManualClock in tests), `OutputAdapter` implementations `ScreenLogAdapter` (logs a "send" record with scheduled and actual time + prints/keeps last N for display) and `MockSerialAdapter`. Late decisions: if submitted after t_send, send MISC immediately with LATE_DECISION and log a miss. Tests with ManualClock: ordering, timing formula (invariant 9) on numbers, late path. Also a real-clock jitter test (mark it so it's skipped on CI-like slow runs only if flaky): 200 sends 5-20 ms apart, report p50/p99/max send error in the task file (target < 10 ms).
- From 002 review: pop due sends ordered by `(t_send_ns, group_id)` so fast replays write send records deterministically. The Scheduler, not the adapter, writes the single "send" record (see contracts.py docs).

## Verification Output
Commit: a7ccaf6 on task/010-schedule-output
Worktree: <REPO_ROOT>/.claude/worktrees/agent-a695e96c556351e57

Commands (all run in the worktree):
- `uv run pytest` -> exit=0, `59 passed in 13.82s` (tests/test_schedule.py: 8 new tests incl. real-clock jitter)
- `uvx ruff@0.17.5 check src tests` -> exit=0, `All checks passed!`
- `uvx ruff@0.17.5 format --check src tests` -> exit=0, `44 files already formatted`
- `uv run sorter --help` -> exit=0 (usage printed, options -h/--config/-v)

Real-clock jitter (200 sends, 5-20 ms apart, spin_ms=4, MonotonicClock), send error = t_actual - t_send, in ms; five runs:
- p50=0.007 p99=0.022 max=0.029
- p50=0.004 p99=0.508 max=9.650 (one OS-preemption outlier while the machine was busy)
- p50=0.004 p99=0.018 max=0.061
- p50=0.005 p99=0.014 max=0.017
- p50=0.005 p99=0.030 max=0.089
Target < 10 ms met in all runs. The test asserts p99 < 10 ms and max < 25 ms (skip with HWSORTER_SKIP_JITTER=1).

Design notes:
- `Scheduler(clock, adapter, log, *, spin_ms, uncalibrated, replay)`. Live: `start()/stop()` thread (coarse wait, then spin on the clock). Fast replay (`replay=True`): no thread; pipeline calls `advance_to(t_ns)` after advancing ManualClock (and `drain()` at the end); due items fire on that thread in `(t_send_ns, group_id)` order. `submit()` in replay mode also fires anything already due. Replay `t_actual_ns` = target send time (submit time for late).
- Late: `t_send_ns < now` at submit -> MISC sent at once, `miss=late_decision`; send log keeps original `t_send_ns`.
- Scheduler writes the single "send" record; `timings = {latency_ms, uncalibrated}` (flag set by caller as `not cfg.timing.calibrated`); `ScreenLogAdapter(uncalibrated=True)` appends "UNCALIBRATED" to lines. Adapter exceptions -> `miss=adapter_error`.
- No config values changed; no contract change needed.


## Review Notes
Verdict: CHANGES REQUIRED (one blocking item, test-policy only; the code is sound).

Verified (worktree a7ccaf6): pytest passed twice (exit 0, jitter p99 0.026 and 0.052 ms, max 0.8 and 1.0 ms); ruff check and format --check clean; `sorter --help` exit 0. No time.* in src/schedule or src/output. Scope is limited to schedule/*, output/*, tests/test_schedule.py.
- send_time_ns (scheduler.py:39-48) is exactly invariant 9. Units are correct (inches / in/s * 1e9, lead ms -> ns, rounded). Camera latency is not applied. A speed <= 0 raises.
- Locking is correct: every heap access is under the Condition, and submit does notify_all. An earlier send submitted while the thread waits for a later one is picked up on the next wake, which is at most 20 ms later, and the spin only starts inside spin_ms. stop() sets the flag under the lock, notifies and joins.
- Late path: submit rewrites the output to MISC and keeps the original t_send_ns. The send record carries scheduled and actual times. Replay and live both behave correctly, and a send at exactly now is not late (tested).
- Replay is deterministic and never sleeps. Order is (t_send_ns, group_id, seq).
- Uncalibrated is flagged in the log timings and on the screen adapter.

1. tests/test_schedule.py:145-166: make the real-clock jitter test opt-in. Change the skipif to `not os.environ.get("HWSORTER_RUN_JITTER")`, and update the task note (line 31) to say so. It asserts wall-clock bounds (p99 < 10 ms, max < 25 ms) with 4 ms of GIL-contending spin. Under CI load or 3 parallel workers an OS preemption can exceed 25 ms, so the default suite would fail intermittently. The orchestrator wants a deterministic default suite. Keep the print of the percentiles for the opt-in run.

OPTIONAL:
a. scheduler.py:174 and :200: `_fire` runs outside any try. `self._log.log` or a `_clock` error would kill the scheduler thread silently. Wrap the loop body with logging.exception, or at least log.
b. scheduler.py:181: an adapter exception sets `miss=ADAPTER_ERROR` and overwrites LATE_DECISION. The late flag and `n_late` are lost for a late send that also failed. The exception text and traceback are never recorded. Consider keeping both, or logging via the `logging` module.
c. scheduler.py:167-169: the final spin runs on a fixed `target`. A send submitted during the last <= 4 ms spin that is earlier than `target` fires up to 4 ms late. This is tolerable. The tight spin also holds the GIL for up to the switch interval, which can delay the pipeline thread's submit slightly.
d. The first-publish `pending()` returns the rewritten MISC item for late entries. That is fine, but document it.
