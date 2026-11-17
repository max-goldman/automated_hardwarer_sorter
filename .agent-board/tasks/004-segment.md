# 004 segmentation + flat-field application

Status: IN_REVIEW
Dependencies: 002
Branch: `task/004-segment` (from `develop`)

## Scope (files the task may touch)
src/hwsorter/vision/segment.py, tests/test_segment.py

## Requirements
Read `<REPO_ROOT>/.agent-board/contracts-spec.md` and `<REPO_ROOT>/.agent-board/board.md` first. Code against `hwsorter.contracts` (do not edit it unless this IS task 002).
Implement `Preprocessor` and `segment()` per contracts-spec.md: flat-field divide (reuse apply_flatfield), slow gain tracker for lighting drift, deviation-from-belt mask OR sharp-edge mask, morph close, fill to outer silhouette, reject shallow soft shadows and specks below segment.min_area_in2 (convert via scale from calibration/PlaneMapper, not constants), sub-pixel contour refinement (simple approach acceptable: cv2 contour + subpixel along normals, or at minimum float contours from a smoothed mask; document what you chose). `touches_edge` when the blob touches the left/right image edge (x direction).
Tests on synthetic.py scenes: each of the six parts segmented as exactly one blob; area within 5% of nominal at the synthetic scale; shadows and specks rejected; two separated parts give two blobs; bright bolt-head facets don't split the bolt. Performance: report mean ms per 1280x800 frame on this Mac in the task file.

## Session note
Session 3+ has no camera and no real recordings. The real-recording check below was done on the session-2 Mac (result: one blob for the lying bolt on steady frames). Any further real-data checks go to `hardware-pending`. Rework per the ADOPTED budget plan (`<REPO_ROOT>/.agent-board/budget-proposal.md`) after 002b lands: lookup table instead of np.isin, single-pass preprocess, two-stage coarse-then-crop, equivalence test for coarse_scale 1 vs 2, target about 3 ms per frame on whatever machine runs it (state the CPU).
Session 2: the Opus reviewer hit the usage limit before writing any notes. A fresh review is needed. See also the budget proposal.

## Verification Output
Worktree: <REPO_ROOT>/.claude/worktrees/agent-a155b4063a90bb775
Branch task/004-segment, commit 060a71c (files: src/hwsorter/vision/segment.py, tests/test_segment.py, plus default-value change of segment.edge_threshold in config.py and config/default.toml).

Commands (final run, all in the worktree):
- `uv sync`: exit 0
- `uv run pytest`: exit 0, tail: `.............................................................. [100%]` (62 passed, 11 in test_segment.py)
- `uvx ruff@0.17.5 check src tests`: exit 0, `All checks passed!`
- `uvx ruff@0.17.5 format --check src tests`: exit 0, `43 files already formatted`
- `uv run sorter --help`: exit 0 (usage + options printed)

Performance (this Mac, synthetic 1280x800 frame with 2 parts, Preprocessor.apply + segment, 30 frames): mean 11.9 to 13.3 ms/frame.

Approach:
- Preprocessor.apply: apply_flatfield, then divide by a slow gain tracker (EMA, gain_alpha, of the 75th percentile of the ROI centre; frames >40% off the tracker ignored). Outside the ROI the flat image is set to 1.0. Steady lighting assumed; no flicker handling (per orchestrator decision).
- segment(p, cfg, mapper=None): mask = |flat-1| > deviation_threshold OR smoothed gradient > edge_gradient_threshold, inside the ROI; plus growth into pixels brighter than belt by edge_threshold that touch the strong mask (recovers bolt-head facets as bright as the belt; darker pixels never grown, so shadows stay out); close; fill holes (also holes with a <9 px leak, so shank highlights and bores cannot make the outline dive in); external contours.
- Rejected: shadow components (peak dev < shadow_max_depth and no sharp edge), blobs under min_area_in2 (px via local Jacobian of PlaneMapper at image centre), and thin strips (<0.15 in tall) hugging the ROI top/bottom (belt-edge shading the smoothed flat-field leaves behind).
- Sub-pixel: integer contour moved along its outward normal (search +-1.5 px) to the half-way crossing between the level just outside (belt or soft shadow) and the highest level just inside of the lightly smoothed |flat-1| profile; median-filtered offsets; area_px is the area of the refined contour. mask_bbox_px is (x0,y0,x1,y1) with x1,y1 exclusive. touches_edge: bbox touches the ROI left/right limits.
- NOTE for task 011 (pipeline): `segment` takes an optional third argument `mapper: PlaneMapper | None` (the contract signature `segment(p, cfg)` has no scale source). The pipeline MUST pass the mapper; without it the min-area is only 8 px and the edge-strip width falls back to 12 px.
- Default-value change: segment.edge_threshold 0.06 -> 0.03 (config.py and default.toml). Justification: it is now the brightness-above-belt level for growing into bright facets; the synthetic bolt-head facet reads about +0.04 above belt (measured), and 0.03 is above the steady-belt noise (about 0.005 after smoothing) while still above the real-belt residual on steady frames (0 spurious blobs on 205 steady real frames, below). Soft shadows cannot be grown into because only brighter-than-belt pixels are grown.
- Synthetic accuracy (area vs rendered silhouette at 20 deg, tolerance 5%): all six parts pass; unrefined/refined differences within about 1.5% for bolts and nuts.

Real recording check (manual, not committed; recordings/20261117-002046-still-belt-1ms, 366 frames, provisional 367 px/in, flat-field built from every 3rd frame, ROI from find_belt_edges = rows 61..800):
- The belt is NOT empty: a bolt sits stationary at about x=180..690, y=290..540 and is segmented as one blob (284 of 366 frames in this run: the rest are dimmed frames where it is merged with or split by extra dark regions).
- Steady frames (level >= 97% of median; 205 frames): 0 extra blobs per frame in all 205 (the bolt is the only blob). Pass.
- Dimmed frames (161), informational only: extra blobs per frame {0: 72, 1: 72, 2: 13, 3: 1, 5: 2, 6: 1}, located at the ROI top/bottom edges (belt-edge shading deeper under dimming, a black-level offset makes dimming non-multiplicative) and near the bolt.
- With an identity flat-field the whole ROI is one blob (falloff of the real belt is large), so a real flat-field is required.
- Caveat: the gain tracker assumes steady lighting; steady and dimmed frames were run through separate Preprocessors, because mixing them with the flickering stream skews the tracker (the steady frames then read bright and the whole ROI becomes a blob).

Manual-check script (scratchpad only, not in the repo; needs tests/test_segment.py's make_mapper on the path):
```python
"""Manual check (not committed): segment() on a stationary belt recording.

Run from the repo root:  uv run python real_check.py <recording folder>
The belt in this recording holds one stationary bolt (found as the big blob near x=180..690).
Counts blobs OTHER than that bolt, split into steady frames (level >= 97% of the median
level) and dimmed frames. Steady frames are the pass/fail set; dimmed are informational. Steady and dimmed frames use
separate Preprocessors (the gain tracker assumes steady lighting).
"""

import sys
from collections import Counter

import numpy as np

sys.path.insert(0, "tests")
from test_segment import make_mapper  # provisional 367 px/in scale

from hwsorter.calibration.flatfield import build_flatfield
from hwsorter.calibration.store import RoiCalibration
from hwsorter.config import SegmentConfig
from hwsorter.recording.store import RecordingReader
from hwsorter.vision.segment import Preprocessor, segment

frames = list(RecordingReader(sys.argv[1]))
roi = RoiCalibration(top=61, bottom=800, left=0, right=1280, source="auto")
levels = np.array([float(f.image[61:800].mean()) for f in frames])
steady = levels >= 0.97 * np.median(levels)
print("frames", len(frames), "steady", int(steady.sum()), "dimmed", int((~steady).sum()))
flat = build_flatfield([f.image for f in frames[::3]])
cfg = SegmentConfig()
pre = Preprocessor(flat, roi, cfg)
mapper = make_mapper()
extra = {True: Counter(), False: Counter()}
bolt_frames = 0
examples = []
pre_dim = Preprocessor(flat, roi, cfg)  # separate tracker so dimmed frames cannot skew the steady one
for f, st in zip(frames, steady, strict=True):
    pre_use = pre if st else pre_dim
    n_extra = 0
    for b in segment(pre_use.apply(f), cfg, mapper):
        x0, _, x1, _ = b.mask_bbox_px
        if 150 < x0 < 220 and 500 < x1 < 720 and b.area_px > 40000:
            bolt_frames += 1
        else:
            n_extra += 1
            if st and len(examples) < 6:
                examples.append((f.seq, b.mask_bbox_px, round(b.area_px)))
    extra[bool(st)][n_extra] += 1
print("frames with the bolt blob:", bolt_frames)
print("extra blobs/frame histogram, steady:", dict(extra[True]), "dimmed:", dict(extra[False]))
print("steady examples:", examples)
```


## Review Notes
(reviewer only)
