# 024 second part size is 5/16-18, not 3/8 (user decision 2026-11-17)

Status: DONE (merged)
Dependencies: none. Runs in parallel with 023 (real-image robustness); whichever merges second merges develop and regenerates goldens.
Branch: `task/024-second-size-5-16` (from `develop`)
Reviewer: Opus (contracts change).

## Decision (user, 2026-11-17)
The in-scope set is 1/4-20 and **5/16-18** hex bolts (x 1 in), hex nuts and SAE flat washers. The brief's "3/8" was an error. The user's nuts are **nylon-insert lock nuts (nyloc)**. Approved contract change: rename the 3/8 PartClass values.

## Caliper truths (user, `hardware-reference-pics/reference-pic-measurements.md`, ±0.03 mm)
| Part | Truth (in) | Standard nominal (in) |
| --- | --- | --- |
| 1/4-20 x 1 bolt | head AF 0.4315, head height 0.170, shank 0.246, total length 1.149 | AF 7/16=0.4375, head 0.163, shank 0.250 |
| 5/16-18 x 1 bolt | head AF 0.495, head height 0.214, shank 0.302, total length 1.205 | AF 1/2=0.500, head 0.211, shank 0.3125 |
| 1/4-20 nyloc nut | AF 0.433, thickness 0.311 | AF 7/16, nyloc thickness ~0.32 |
| 5/16-18 nyloc nut | AF 0.495, thickness 0.347 | AF 1/2, nyloc thickness ~0.36 |
| 1/4 SAE washer | ID 0.283, OD 0.624, thick 0.047 | 0.281 / 0.625 |
| 5/16 SAE washer | ID 0.346, OD 0.751, thick 0.047 | 0.344 / 0.750 |

## Naming
`bolt_0312`, `nut_0312`, `washer_0312` replace `bolt_0375`, `nut_0375`, `washer_0375` (keep the `_025` style: thousandths truncated). The existing out-of-scope synthetic `washer_0312` (OD 0.688) must be renamed (e.g. `washer_oos_0688`) so names don't collide. Out-of-scope near-miss items for tests become the 3/8 parts (`washer_0375` OD 0.812/ID 0.406, `nut_0375` AF 0.5625, `bolt_0375`) plus `coin` — they are the natural near-misses of 5/16.

## Scope
`src/hwsorter/contracts.py` (PartClass values only; also any docstrings), `synthetic.py` (PART_DIMS + PART_KINDS + nyloc nut rendering: thickness for side pose, and an optional lighter nylon-insert annulus inside the bore from the top, configurable), `config/parts_library.yaml` (5/16 entries with display names, output codes unchanged by position: bolt_0312 takes bolt_0375's code, etc.; ranges re-derived from synthetic values with the same method/tolerance as 008/018; nut side pose thickness from nyloc truths), `bench.py`, `measure.py`, `gui/grading.py` (any class lists), all tests, goldens (regenerate with `scripts/update_goldens.py`, justify), `docs/design.md`, `README.md`, `initial_progress.md` (decision row + note), `descriptions/Hardware Sorter Vision Program Project Brief.md` and `descriptions/Meta-Project-Description` (replace the 3/8 size with 5/16-18 wherever it names the part set; change nothing else; list every edit in the report). Do NOT edit `.agent-board/` (orchestrator does), historical task files, or vision algorithms (023 owns segment/features/seam).

## Requirements
- No remaining `0375`/`3/8` reference that means an in-scope part (grep and list leftovers with justification: 3/8 near-miss items are fine).
- Acceptance still holds on synthetic: 30-part end-to-end (now 1/4 + 5/16) = 30/30, zero contamination; the 5/16-vs-3/8 near-miss items come out OUT_OF_SCOPE or UNRESOLVED, never a labelled bin. Report the closest margins between 5/16 and 3/8 and between 1/4 and 5/16 features (they are closer than 1/4 vs 3/8 were: e.g. nut AF 0.4375 vs 0.500, washer OD 0.625 vs 0.750 vs 0.812).
- Stationary tests, determinism, stride guard pass.

## Verification
pytest fully green; ruff check + format --check exit 0; `sorter run --source synthetic --headless --fast --parts 30`; `sorter measure synthetic`; grep listing.

## Verification Output
Round 1 worker b3badb1: pytest exit=0 (2 xfail incl. new ghost repro), ruff/format exit=0, 30-part run exit=0; margins 1/4 vs 5/16 >= 1.94, 3/8 nut vs 5/16 1.88.

## Review Notes
Round 1 (fresh Opus): CHANGES REQUIRED. Rename complete; dims correct; goldens legit; brief edits size-only. CONTAMINATION: nut_025/nut_0312 ranges touch (AF 0.47/0.47, area 0.19/0.19, side 0.33/0.33) -> a +5% scale makes nut_025 CONFIDENT nut_0312. Required: gapped nut ranges (reviewer-tested set) + general scale-sweep safety test (no confident cross-class flip within ±7%). Also: strict xfail for the seam ghost, the brief's 1/4 nut thickness 0.311, a design.md near-miss limit. Pre-existing seam ghost (track 14 -> 2 parts to REWORK in the 30-part CLI run) is owned by 023.

Round 2 (f9530e2) and round 3 (3074185): gapped nut ranges, test_library_safety.py (no wrong CONFIDENT within ±7%), merged 023, ghost test passing, circularity nut [0.88,0.965] / washer [0.975,1.02], bolt sizes separated by shank only. Real: all six recordings CONFIDENT and correct (tightest real margin: nut circularity 0.952 vs 0.965). Round 3 review: orchestrator — merged, pytest exit=0, ruff/format exit=0; stationary replays of the six real recordings give bolt_025, bolt_0312, nut_025, nut_0312, washer_025, washer_0312. Known: CLI 30-part synthetic run group 28 (washer ON_SEAM, 2 frames) -> misc, pre-existing seam issue (follow-up).
