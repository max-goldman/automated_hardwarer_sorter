# Processing-budget plan: ADOPTED (2026-11-17)

**Decision:** the user agreed that not every frame needs processing ("unless that will really bring significant accuracy gain"; 10 or fewer frames per part is assumed fine) and left the numbers to the orchestrator. Adopted as proposed by the Opus advisor:
- `pipeline.frame_stride` = 2
- `pipeline.feature_stride` = 3
- `pipeline.max_feature_frames` = 10
- `pipeline.queue_frames` = 120
- `segment.coarse_scale` = 2
- `segment.crop_pad_px` = 16

**Accuracy guard:** task 015 must include a synthetic comparison of stride 1 vs 2 and of max_feature_frames 10 vs 20. Fused feature spread and classification accuracy must not get meaningfully worse (for example, accuracy drops by no more than 0.5 percentage points and median feature error grows by no more than 0.002 in). If they do, revisit.

Task 002b adds the config keys; the 004 rework applies the segment speedups; 011 applies the strides.

## Original proposal

Problem: on the Dell (about 2.5x M5), segmentation is about 31 ms per frame and features about 4-11 ms per part per frame, with up to 3 parts in view, so about 60 ms per frame against an 8.3 ms budget.

1. Frame stride: process a frame only when `seq % frame_stride == 0`, default 2 (a 16.7 ms budget). Tracking gates are fine (motion-compensated); track frame counts become counts of processed frames.
2. Features on a subset: valid observations where `(seq//frame_stride) % feature_stride == 0` (default 3), capped at `max_feature_frames` = 10 per track, extracted as frames arrive. Deterministic.
3. Threads, single process. Switch to multiprocessing (a shared_memory ring) only if the bench shows queue drops above 0, or live pipeline time more than 25% above replay time.
4. segment.py speedups, in order:
   - Replace `np.isin` with a lookup table.
   - Precompute `inv_flat` and do a single `cv2.multiply` preprocess.
   - Two-stage segmentation: pyrDown and inRange coarse detection, then the existing full-res code on padded crops (`crop_pad_px` = 16).
   - Add an equivalence test comparing `coarse_scale` 1 vs 2.
5. Dell budget per processed frame: preprocess 1.5 ms, coarse 1.0, crops 4.0, track 0.5, features amortised 4.5, fuse/decide/log 0.5. About 12 of 16.7 ms, roughly 72% utilisation.
6. New config keys (need sign-off):
   - `pipeline.frame_stride` = 2
   - `pipeline.feature_stride` = 3
   - `pipeline.max_feature_frames` = 10
   - `pipeline.queue_frames` = 120
   - `segment.coarse_scale` = 2
   - `segment.crop_pad_px` = 16
   A queue overflow is a FAIL, and replay blocks instead of dropping.
7. Bench (015), run on the Dell:
   - Full speed: busy time at most 6.2 ms per camera frame, and under 1.0 s in the worst 1 s window.
   - Paced at 120 fps: latency p99 at most 250 ms, zero overflows.
   - Determinism: decision logs identical across runs, apart from timings.
   - Header: CPU, cv2 threads, Python version, strides.
