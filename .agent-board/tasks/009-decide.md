# 009 grouping + decision rules

Status: DONE
Dependencies: 002
Branch: `task/009-decide` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/decide/*.py, tests/test_decide.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it unless this IS task 002).
Implement `Grouper`, `decide()`, `Decider`, and `correct_output()` in decide/rules.py per contracts-spec.md invariants 5, 6, 8. Unit tests: every row of the brief's correct-output table, plus edge cases: side-by-side parts (gap 0), chain linking A-B-C where A-C > gap, exactly-at-gap boundary, group closure only after rework_gap of empty belt, flush closes all, lone UNRESOLVED -> MISC, group with UNRESOLVED member -> REWORK, all OUT_OF_SCOPE group -> MISC.

## Verification Output
(worktree: <REPO_ROOT>/.claude/worktrees/agent-a15ea40da9488d16c ; commit 500570f on task/009-decide)

- `uv run pytest` exit=0: `81 passed in 22.12s`
- `uvx ruff@0.17.5 check src tests` exit=0: `All checks passed!`
- `uvx ruff@0.17.5 format --check src tests` exit=0: `43 files already formatted`
- `uv run sorter --help` exit=0 (usage + subcommands listed)

Files: src/hwsorter/decide/rules.py, decide/__init__.py, tests/test_decide.py.
API: Grouper(rework_gap_in).add/advance(entry_belt_x_in,t_ns)/flush(); decide(); decide_with_rule() -> (Output, rule); Decider(rework_gap_in).add/advance/flush(t_ns) -> list[GroupDecision]; correct_output(labels). Group ids start at 1, monotonic. Closed groups are returned most-downstream first, members in track_id order. Pipeline (011) must only call advance when no live track lies in the span behind the group.

| Brief row / edge case | Test |
| --- | --- |
| One in-scope part | test_row_one_in_scope |
| One out-of-scope part | test_row_one_oos |
| Several same class | test_row_same_class_group |
| Mixed classes | test_row_mixed_classes |
| In-scope + out-of-scope | test_row_in_scope_plus_oos |
| Only out-of-scope | test_row_only_oos, test_all_oos_group_misc_via_decider |
| Lone UNRESOLVED -> MISC | test_lone_unresolved_is_misc |
| Group with UNRESOLVED/MERGED -> REWORK | test_group_with_unresolved_is_rework, test_merged_flag_member_unresolved_is_rework |
| Rule ids match RULES | test_rules_ids |
| correct_output rows | test_correct_output, test_correct_output_empty_raises |
| Side-by-side gap 0 | test_side_by_side_gap_zero_linked |
| Chain A-B-C, A-C > gap | test_chain_links_a_c_beyond_gap, test_chain_order_independent_of_add_order, test_long_part_bridges_short_neighbours |
| Exactly at gap boundary | test_exactly_at_gap_not_linked, test_just_under_gap_linked |
| Closure only after rework_gap empty belt | test_closure_only_after_gap_of_empty_belt, test_closure_uses_group_trail_and_waits_for_chain, test_advance_closes_only_ready_groups_downstream_first |
| flush closes all | test_flush_closes_all |
| t_ref/x_lead from most downstream member | test_decider_group_fields_from_most_downstream_member |
| Monotonic deterministic group ids | test_decider_group_ids_monotonic_deterministic |


## Review Notes
Verdict: APPROVE

Re-run in worktree (500570f): pytest exit=0 (81 passed), ruff check exit=0, ruff format --check exit=0, `sorter --help` exit=0.

Verified against brief:
- decide_with_rule (rules.py:40-54): lone confident -> class bin; lone OOS or UNRESOLVED -> MISC, never REWORK. Group: all OOS -> MISC; all confident same class -> class bin; everything else (mixed classes, in-scope + OOS, any UNRESOLVED) -> REWORK. Matches the table and bullets.
- correct_output (rules.py:21-33): lone in-scope -> class, lone OOS -> MISC, all-None group -> MISC, same class -> class, mixed or in-scope + OOS -> REWORK. A separate lone/group flag is not needed: the all-equal test gives the same result as the brief for both cases.
- Grouping (rules.py:81-94): interval-merge sweep by trail with reach = max lead of the cluster. Gap is along x only, so it is transitive, and overlap gives a negative gap, which links (same as clamping to 0). Gap exactly equal to rework_gap is not linked (strict `<`). Independent of add order (sort key trail, track_id). No time.* calls, no set iteration order (the `classes` set holds one element in the only branch that reads it).
- Closure (rules.py:104-111): `entry <= min trail - gap`. Equality means not linked, consistent with the boundary rule. flush closes all. Group ids are monotonic from 1, and release order is deterministic (-max lead, first track_id). t_ref and x_lead come from max lead, ties to the lowest track_id (rules.py:136).

OPTIONAL (non-blocking):
1. rules.py:104 Grouper.advance: the caller must guarantee no live track is in the span behind a group, which is a pipeline foot-gun for 011. Cheap and cleaner fix: pass `frontier_x_in = max(entry_belt_x_in, max live-track belt_x lead)` (or the list of live leads) and close a group only if `frontier <= min trail - gap`. This also covers a live track that will bridge into a group. It costs one argument and a couple of tests. If it is not done, 011 must call advance only when no live track overlaps or sits just behind a group, and document that.
2. rules.py:104/128 `t_ns` is unused in Grouper.advance (ruff allows it). Drop it from Grouper, keep it on Decider.
3. rules.py:88 With rework_gap_in <= 0 nothing but strict overlap links. The config should validate it as > 0 (config, not 009).
