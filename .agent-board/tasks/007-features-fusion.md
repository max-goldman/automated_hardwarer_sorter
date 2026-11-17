# 007 feature registry, features, fusion

Status: DONE (merged into develop, session 3)
Dependencies: 002
Branch: `task/007-features-fusion` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/vision/features/*.py (except __init__ already created; you may fill __init__.py), src/hwsorter/vision/fusion.py, tests/test_features.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it unless this IS task 002).
Implement the feature registry and features per contracts-spec.md and the approved plan item 7: area_in2, minrect_long_in, minrect_short_in, hull_solidity, circularity, hex_harmonic (six-fold radial profile harmonic), across_flats_in, across_corners_in, hole_kind (0 none / 1 bright / 2 dark) and hole_diameter_in (washer holes image bright, nut bores image dark: two detectors on Prepared), bolt profile (shank_width_in, head_width_in, head_length_in, total_length_in; NaN for non-elongated parts), edge_sharpness. Thread pitch: reserved name only, not built. Fusion: per feature median + MAD over valid observations, ignoring NaN. Tests on rendered shapes of known size (use synthetic.py and/or draw your own hex/ring/bolt with blur+noise): each feature within tolerance (state tolerances), 1/4 washer vs 3/8 nut separable by hex_harmonic and hole_kind. Report extract() ms per part.
- From 002 review: hole detectors (bright >= features hole threshold 0.90, dark <= 0.30) must be skipped for elongated parts (bolts). Darkest bolt pixels measure 0.28-0.29, just under 0.30. Use the features-section elongation ratio to gate them.

## Session note
Session 2: the Opus reviewer hit the usage limit before writing any notes. A fresh review is needed.

## Verification Output
Worktree: <REPO_ROOT>/.claude/worktrees/agent-a1da80c9fce84ba3b
Commit: f5b9eb6 on `task/007-features-fusion` (from develop d81715e)

| Command | Exit | Tail |
| --- | --- | --- |
| `uv run pytest` | 0 | `102 passed in 34.75s` (52 in tests/test_features.py) |
| `uvx ruff@0.17.5 check src tests` | 0 | `All checks passed!` |
| `uvx ruff@0.17.5 format --check src tests` | 0 | `47 files already formatted` |
| `uv run sorter --help` | 0 | usage printed (subcommands incl. calibrate; options --config, -v) |

### Design notes
- Signatures: `extract(obs, prepared, cfg=None)`, `fuse(track, vectors, cfg=None)` (cfg = FeaturesConfig; defaults used if omitted). Registry `@register(name, provides=(...))`; groups take `(contour_in, prepared, blob, cfg)`. `feature_names()` order: area_in2, minrect_long_in, minrect_short_in, hull_solidity, circularity, hex_harmonic, across_flats_in, across_corners_in, hole_kind, hole_diameter_in, shank_width_in, head_width_in, head_length_in, total_length_in, edge_sharpness. `thread_pitch_in` only in `RESERVED_FEATURES`, never emitted.
- Elongated (minrect long/short > elongated_min_aspect): hole detectors skipped (hole_kind=0.0, hole_diameter_in=NaN); hex_harmonic NaN; bolt profile computed. Non-elongated: bolt profile NaN. No hole: hole_kind 0, diameter 0.0.
- hole_kind: 1 bright (flat >= 0.90), 2 dark (<= 0.30); the larger component inside the silhouette (eroded by 3% of sqrt(area) px) wins; hole_min_diameter_in applies.
- across_flats = min caliper width, across_corners = max Feret of the hull. edge_sharpness = median over the contour of peak |grad flat| (per px, 5x5 dilated); falls with blur (tested).
- fuse: median + MAD over valid vectors ignoring NaN; n_frames = valid count; fewer than features.min_valid_frames (3) gives all-NaN medians; on_seam from TrackFlag.ON_SEAM or any blob.on_seam.
- No config default values were changed.

### Measured vs nominal (synthetic, noise+blur 1.1px, mean over angles 0/17/45/90/133 deg; "maxerr" = worst |measured-nominal| over those angles)
Silhouette = ground-truth alpha at 1/4 px (tests also run a threshold+findContours contour for washer/nut).
| part | feature | measured | maxerr | nominal |
| --- | --- | --- | --- | --- |
| bolt_025 | minrect_long / short | 1.1564 / 0.5057 | 0.0008 / 0.0011 | 1.156 / 0.505 |
| bolt_025 | shank / head_w / head_len / total | 0.2433 / 0.5055 / 0.1553 / 1.1506 | 0.0071 / 0.0007 / 0.0007 / 0.0058 | 0.250 / 0.505 / 0.156 / 1.156 |
| bolt_0375 | minrect_long / short | 1.2349 / 0.6505 | 0.0015 / 0.0010 | 1.234 / 0.650 |
| bolt_0375 | shank / head_w / head_len / total | 0.3674 / 0.6503 / 0.2335 / 1.2288 | 0.0078 / 0.0008 / 0.0006 / 0.0058 | 0.375 / 0.650 / 0.234 / 1.234 |
| nut_025 | area / AF / AC | 0.1662 / 0.4382 / 0.5050 | 0.0001 / 0.0006 / 0.0009 | 0.1661 / 0.438 / 0.5058 |
| nut_025 | hex_harmonic / circularity / solidity | 0.0581 / 0.9173 / 0.9969 | n/a / 0.0105 / 0.0039 | ~0.066 / 0.9069 / 1.0 |
| nut_025 | hole_kind / bore dia | 2 / 0.1946 | 0 / 0.0065 | 2 / 0.201 |
| nut_0375 | area / AF / AC | 0.2747 / 0.5631 / 0.6490 | 0.0002 / 0.0007 / 0.0012 | 0.2745 / 0.563 / 0.650 |
| nut_0375 | hex_harmonic / circularity / solidity | 0.0581 / 0.9173 / 0.9982 | n/a / 0.0107 / 0.0022 | ~0.066 / 0.9069 / 1.0 |
| nut_0375 | hole_kind / bore dia | 2 / 0.3059 | 0 / 0.0073 | 2 / 0.313 |
| washer_025 | area / OD(minrect) / AC | 0.3060 / 0.6240 / 0.6251 | 0.0008 / 0.0010 / 0.0001 | 0.3068 / 0.625 / 0.625 |
| washer_025 | hex_harmonic / circularity / solidity | 0.0000 / 1.0002 / 0.9985 | 0.0001 / 0.0003 / 0.0016 | 0 / 1 / 1 |
| washer_025 | hole_kind / hole dia | 1 / 0.2454 | 0 / 0.0356 | 1 / 0.281 |
| washer_0375 | area / OD(minrect) / AC | 0.5162 / 0.8106 / 0.8117 | 0.0017 / 0.0021 / 0.0003 | 0.5178 / 0.812 / 0.812 |
| washer_0375 | hex_harmonic / circularity / solidity | 0.0000 / 1.0002 / 0.9987 | 0.0001 / 0.0003 / 0.0013 | 0 / 1 / 1 |
| washer_0375 | hole_kind / hole dia | 1 / 0.3743 | 0 / 0.0319 | 1 / 0.406 |

Test tolerances: area 0.004 in2; minrect/AF/AC/head_width/head_length 0.004 in; total_length 0.010; shank 0.012; solidity 0.010; circularity 0.015; hole_kind exact; bore dia 0.012; washer hole dia 0.045. Known biases: thread valleys make shank read ~0.007 narrow; the bright-hole threshold 0.90 reads washer holes ~0.035 in small because the part's soft shadow darkens the hole rim (classify ranges should be derived from measurement, so the bias is harmless; threshold left at 0.90 since nut rims reach ~0.86 flat and a lower threshold risks false bright holes). Hex vs round: nut hex_harmonic 0.058 vs washer 0.0000-0.0001 (asserted > 0.04 vs < 0.01), circularity nut 0.917 vs washer 1.000. 1/4 washer vs 3/8 nut separated at 6 angles (0,17,30,45,90,133) by hole_kind (1 vs 2), hex_harmonic, circularity while across_corners differ by <0.04 in. Rotation invariance tested (nut_025, washer_0375, bolt_0375 at 0/22/61/118 deg, spread < 0.008).

### extract() time per part (this laptop, contour of ~2000-5000 pts from the 4x test silhouette)
nut_025 1.7 ms, nut_0375 2.2 ms, washer_025 3.5 ms, washer_0375 ~4.3 ms (up to 7.6 ms under load), bolt_025 4.3 ms, bolt_0375 4.5 ms. Test asserts < 50 ms. Real contours are shorter, so expect lower.


## Review Notes
Session 3 fresh Opus review: APPROVE, no changes required. 102 tests pass; clean in-scope diff; no Qt import; contract-compliant registry/fuse.
Authoritative feature_names() (15, by runtime capture): area_in2, minrect_long_in, minrect_short_in, hull_solidity, circularity, hex_harmonic, across_flats_in, across_corners_in, shank_width_in, head_width_in, head_length_in, total_length_in, hole_kind, hole_diameter_in, edge_sharpness. (The "design notes" order above lists the hole block before the bolt block — STALE; key by name.) Perf ~4 ms/part (bolts ~7 ms) on the 2-core Xeon; 011 caps frames/part, bench (015) is the gate. See board "Notes for task 011".
