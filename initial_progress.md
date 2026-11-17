# Hardware Sorter Vision Program: Initial Progress

Written 2026-11-16 at the end of the first working session. Nothing has been committed to git; every file below is new and untracked.

## Where things stand

- **Planning:** done. The architecture and milestone plan was approved and is reproduced in full at the end of this file.
- **Milestone 0 (camera spike):** done on the Mac, with two hardware findings that need action (flickering lights, low light level).
- **Milestone 1 (capture, record/replay, live view, calibration):** the software is written and its 34 automated tests pass. It has not yet been used on the rig with a printed target or a moving belt, and the README is still a stub.
- **Milestones 2 and 3:** not started.

## Decisions made with the user

| Topic | Decision |
| --- | --- |
| Deployment OS | Linux on the Dell (a Raspberry Pi is a possible later target) |
| GUI | Qt desktop app (PySide6) |
| Lighting | May be adjusted if recordings show a problem |
| Belt | One stitched diagonal seam; belt about 52 in long, so the seam passes about every 13 s |
| Runway | About 12 in from the camera view to the collector |
| Belt motion | Stationary for now; can run on request |
| Out-of-scope test items | Undecided, so tolerances are designed for near-miss sizes |
| Checkpoints | Stop after each milestone for a hardware checklist (my default; not explicitly confirmed) |

## Milestone 0 results (measured on the Mac)

| Check | Result |
| --- | --- |
| 1280 x 800 at 120 fps | Works: 120.6 fps, no dropped frames over about 7,000 frames |
| Python capture path | Native macOS camera API through PyObjC. It gives driver timestamps on the same clock as Python's monotonic clock and costs about 0.1 ms per frame. OpenCV reaches 120 fps but has no timestamps; PyAV only gets the camera's 10 fps mode |
| Fixed exposure | Works over USB control requests with no admin rights, in 0.1 ms steps, 0.2 ms minimum. The first write after switching to manual can be ignored, so every write is read back and retried |
| Gain | The control accepts values but does not change the image |
| Light level | Belt read 63 of 255 at 0.5 ms and 117 at 1.0 ms in the first test (109 at 0.5 ms in a later one) |
| Flicker | 32 to 42% of frames dimmed; the worst are 87 to 94% down at 0.5 ms, 37% at 1 ms, 11% at 2 ms |
| Stream hiccup | Switching exposure mode stalls the stream for about 0.2 s; these gaps are now logged as expected, not as lost frames |
| Recording codec | zstd level 1 on decoded pixels: about 8x smaller than raw, about 2 ms per frame, roughly 1 GB per minute |
| Timer accuracy | A plain sleep overshot by 3 ms on macOS, so the clock now busy-waits the last 4 ms |

Not checked: mirroring (the calibration target detects it automatically) and motion blur (needs a moving belt).

## Open hardware issues

1. **Lighting flicker.** *Update 2026-11-17: the user deferred this; the lighting will be fixed later, and the software assumes steady lighting until then.* The pattern looks like lights that switch fully off for about 0.4 to 0.5 ms at a time, which is typical of PWM dimming. I could not rule out the camera itself. Question still open: how are the lights powered or dimmed? Running a dimmer at 100% or using a constant-current supply would likely fix it.
2. **Light level.** Gain does not work, so brightness must come from light or exposure. A 0.5 ms exposure (0.73 px of blur) needs roughly two to three times more light to reach the target belt level of 180.
3. **Camera view.** During the last tests the camera saw a featureless surface with no dark belt edges, and by the end a black image (reading 0 of 255). The rig needs to be back in its working position with lights on before checkpoint 1.

## What was built

All code is under `src/hwsorter/` (about 3,300 lines) with tests under `tests/` (about 500 lines).

| File or folder | What it does |
| --- | --- |
| `pyproject.toml`, `uv.lock`, `scripts/setup.sh` | Python 3.13 environment managed by `uv`; one-step setup |
| `config/default.toml`, `config.py` | Every tunable, validated; unknown keys are rejected. Layering: defaults, `config/local.toml`, optional extra file |
| `clock.py` | Monotonic clock with accurate sleep-until, and a manual clock for replay and tests |
| `frames.py` | `Frame` type and the `FrameSource` interface shared by all sources |
| `camera/avfoundation.py` | macOS live capture with driver timestamps and optional image flips |
| `camera/uvc.py` | Fixed exposure over USB control requests |
| `camera/base.py` | `CameraControl` interface and platform dispatch (Linux backend is a placeholder until milestone 5) |
| `capture.py` | Capture thread that fans frames out to bounded per-consumer queues; counts and logs every gap, driver drop and queue overflow; tracks per-frame belt brightness |
| `eventlog.py` | JSON-lines structured log |
| `recording/` | Lossless recording writer (own thread), reader that tolerates a truncated file, and a replay source (real-time, fast, or looping) |
| `synthetic.py` | Rendered belt scenes with the six parts at nominal size, shadows, specks, a stitched seam, bright bolt-head facets, and optional flicker |
| `calibration/store.py` | Per-machine calibration file (`config/calibration/<machine>.json`) and pixel-to-inch mapping |
| `calibration/target.py` | Printable ChArUco target PDF (two copies per page, with a 50 mm scale bar) |
| `calibration/scale.py` | Target detection, lens distortion and belt-plane fit, mirror detection |
| `calibration/focus.py` | Edge-width focus measure in pixels plus a relative score |
| `calibration/exposure.py` | Motion blur, flicker statistics, plain-language advice |
| `calibration/roi.py` | Automatic belt-edge detection |
| `calibration/flatfield.py` | Illumination map from empty-belt frames |
| `session.py` | Ties source, camera control, capture and recorder together for the CLI and GUI |
| `cli.py` | `sorter camera-info`, `gui`, `record`, `replay`, `recordings`, `calibrate target`, `calibrate show` |
| `gui/` | Qt app: live video with Live, Exposure, Focus, Scale and Belt panels |

### Verified so far

- `uv run pytest`: 34 tests pass. They cover config, clocks, capture accounting, recording round trips, replay pacing, scale and distortion recovery on a rendered target, focus, exposure, belt edges, flat-field, and the Qt app driven off-screen.
- Against the real camera: `sorter camera-info` reports 120.6 fps with no unexplained missing frames, and a 4 s recording read back bit-exact and replayed at about 800 fps.
- The GUI was run off-screen on both the camera and synthetic sources; it has not been opened in a real window or used by a person.

## Last task in progress

I had just fixed two problems found by running the app off-screen:

- Parts passing under the camera were being read as lighting flicker. Per-frame brightness now uses the 75th percentile of the image centre instead of the mean.
- The stream stall at startup and on exposure changes was counted as lost frames. The capture service now takes an "expect disruption" notice and logs those gaps separately.

Both are in place and tested. The next step was to finish milestone 1.

## Remaining work

**To finish milestone 1**

- Write the README: setup on macOS, Linux and Windows, and how to run each tool.
- Add a command to build the flat-field from an empty-belt recording (the function exists; the command does not).
- Open the GUI in a real window on the Mac and try each panel by hand.
- Checkpoint 1 at the rig:
  - Add light. (Flicker deferred by the user on 2026-11-17: the lighting will be fixed later; software assumes steady lighting.)
  - Run `uv run sorter calibrate target`, print at 100%, measure the scale bar.
  - In `uv run sorter gui`: set exposure, focus, capture three target views and solve scale, save belt edges.
  - With the belt running: record the empty belt (45 s), then two or three runs of the 30-part set, including parts placed on the seam.
- Provisional values to settle at checkpoint 1: the focus thresholds (2.2 and 3.2 px) and the nominal 367 px/in scale.

**Milestone 2:** segmentation, seam handling, tracking, features, fusion, grading tool, decision log.

**Milestone 3:** parts library, classifier, grouping and decision rules, scheduler with on-screen output, report, benchmark, regression tests, design note.

**Later:** serial protocol and timing calibration (milestone 4); Linux camera backend and acceptance on the Dell (milestone 5).

---

# Approved plan (in full)

## Context

The repo holds only the project brief and seven reference photos. This plan covers the brief's "first session" scope: everything except serial output (capture, record/replay, calibration, segmentation, tracking, measurement, classification, grouping, decision, scheduling to screen and log, grading, reporting). Acceptance: a graded dry run of at least 300 parts at 95% accuracy and at most 1% bin contamination, within 250 ms p99, from config alone.

### Facts settled before coding

| Item | Value | Source |
| --- | --- | --- |
| Camera mode | 1280 x 800 at 120 fps works on the Mac: 120.6 fps delivered over 5 s, no gaps over 12.5 ms, no drops | Measured through AVFoundation |
| Pixel format on macOS | Frames arrive already decoded (NV12); the luma plane is the mono image, so no MJPG decode in our code on the Mac | Measured |
| Camera ID | Sonix USB 2.0 camera, vendor 0x0c45, product 0x636d | Measured |
| Manual exposure on macOS | AVFoundation offers only "locked" and "continuous auto"; a fixed exposure needs direct UVC control requests | Measured; since verified working in milestone 0 |
| Native scale | About 367 px/in, view about 3.5 x 2.2 in, about 12 px of travel per frame | Estimate: Photo Booth cropped to 3:2 and upscaled 1.2x |
| Deployment OS | Linux on the Dell (Raspberry Pi possible later) | User |
| GUI | Qt desktop app (PySide6) | User |
| Lighting | Adjustable if recordings show outlines breaking | User |
| Belt | One stitched diagonal seam, belt about 52 in long, so the seam passes about every 13 s and roughly 2 to 3% of parts will sit on it | User, `belt-stitching.jpg` |
| Runway | About 12 in from the view to the collector (about 3 s), so decisions can use every frame and wait for the gap behind a part | User |
| Belt motion | Stationary now; can run on request | User |
| Out-of-scope test items | Undecided, so design for near-miss sizes | User |
| Pacing | Stop at each milestone with a hardware checklist | My default, not yet confirmed |

Still unknown (config placeholders, shown as "uncalibrated" on screen and in logs): collector lead time, camera latency, exact runway length, belt speed.

## Architecture

### Stack

- Python 3.13 managed by `uv` (`pyproject.toml` + `uv.lock`); setup on any machine is `scripts/setup.sh`, which installs `uv` if missing and runs `uv sync`.
- `opencv-python-headless` (the headless build avoids a Qt clash with PySide6), NumPy, PySide6, pydantic for config validation, pytest. `pyserial` arrives with milestone 4.
- The pipeline core never imports Qt, so headless runs need no display.

### Threads

| Thread | Job |
| --- | --- |
| Capture | Grab frames, stamp each with a monotonic capture time and sequence number, detect gaps, push to a bounded queue. Any drop is counted and logged, never silent. |
| Recorder (optional) | Write frames to disk from its own queue so disk stalls can't block capture. |
| Pipeline | Segment, track, measure, fuse, classify, group, decide; hand decisions to the scheduler. |
| Scheduler | Priority queue ordered by send time on a monotonic clock; sleeps then spins for the last few ms; calls the output adapter. A decision not ready by its send time sends Miscellaneous and logs a miss. |
| GUI (optional) | Qt main thread; pulls the latest pipeline snapshot at 30 Hz or less. |

Risk: Python's interpreter lock can delay the scheduler thread by a few ms. I measure send jitter with the mock adapter in milestone 3; if it exceeds the 10 ms budget, the scheduler moves to its own process behind the same interface.

### Pipeline stages (identical for live, replay and benchmark)

1. **Frame source** (`camera/`): `FrameSource` and `CameraControl` interfaces. Implementations: macOS AVFoundation, Linux V4L2 (milestone 5), replay, and synthetic.
2. **Flat-field and region of interest**: per-pixel illumination map from the temporal median of an empty belt lap; each frame is divided by it so the belt reads about 1.0 everywhere. A slow gain tracker follows lighting drift. Belt edges are found automatically and tolerate the edge step at the seam join.
3. **Segmentation**: deviation-from-belt mask combined with a sharp-edge mask, closed and filled to the outer silhouette. Shadows are rejected because they are shallow and soft-edged; specks by minimum size. Final outlines are refined to sub-pixel along the contour normals.
4. **Seam handling** (`vision/seam.py`):
   - Detect the seam by geometry: a band spanning the belt width at a fixed angle and width. It never becomes a part and never joins a group.
   - Learn a seam template from empty laps during calibration; refresh it slowly from seam passes that carry no parts.
   - When a part overlaps the seam, register the template to the visible seam and subtract it to recover the part outline. Such parts are flagged `on_seam` and carry wider measurement uncertainty.
   - Use the seam as a once-per-lap marker: continuous belt-speed measurement with no parts on the belt, plus a repeatable focus and lighting check.
5. **Tracking**: parts are stationary in belt coordinates (image position minus speed times time), so association is a gated nearest-match on the predicted position. Only frames with the part fully in view, moving at belt speed and with a stable outline contribute measurements. Merged blobs are flagged unresolvable; parts that never settle are flagged.
6. **Geometry**: undistort contour points only (cheap), then map to inches on the belt plane with a homography from the calibration target.
7. **Features** (`vision/features/`, a registry so new features need no pipeline changes): area, minimum-area rectangle, hull solidity, circularity, radial profile with its six-fold harmonic (round versus hex, across flats and corners), hole kind and diameter (washer holes image bright, nut bores image dark, so two detectors), bolt width profile along its axis (shank width, head width, head length, total length), and edge sharpness. Thread pitch has a reserved slot and is not built.
8. **Fusion**: per track, per feature, median and spread over valid frames (about 65 frames for a bolt).
9. **Classification** (`classify/`, pluggable `Classifier` interface): a range classifier scores each part and pose in the library by its worst-fitting feature. A part is *confident* when one pose fits within the confidence threshold, no other part fits, enough frames were used and the track is clean; *clearly out of scope* when nothing comes close; otherwise *unresolved*.
10. **Grouping and decision** (`decide/`): link parts whose gap along the belt is under the rework gap (1.0 in); a group closes once that much empty belt has been seen behind it. Rules follow the brief's table exactly: lone part goes to its bin if confident, else Miscellaneous; a group goes to a bin only if every member is confidently the same class, to Miscellaneous only if every member is clearly out of scope, else Rework.
11. **Scheduling and output** (`schedule/`, `output/`): one output event per group, timed by `t_send = t_capture + d_remaining / v - t_lead`, carrying its member part IDs. `OutputAdapter` implementations now: screen-and-log, and mock serial for tests.

### Data and config

- `config/default.toml`: every hand-edited tunable, with a schema version.
- `config/calibration/<machine>.json`: written only by the calibration tool (scale, distortion, region of interest, flat-field, seam template, belt speed, lap length, timing values). Tools never rewrite hand-edited files.
- `config/parts_library.yaml`: per part an ID, display name, output code, and feature ranges per pose. Ranges start from nominal dimensions with generous tolerance, then `derive-ranges` recomputes them from graded examples. A side-lying nut is a second pose, added once examples exist; until then it goes to Miscellaneous.
- **Recordings**: a folder per recording holding losslessly stored 8-bit mono frames, an index of sequence numbers and capture times, and a snapshot of config, calibration and git commit. Storing decoded pixels (not MJPG) makes Mac and Dell replays bit-identical. (Decided in milestone 1: zstd level 1, about 1 GB per minute.)
- **Decision log**: one JSON line per part and per group with features, confidence, flags and stage timings.

### Tools (one Qt app plus a CLI: `sorter run | record | replay | calibrate | grade | report | bench | camera-info`)

- **Live debug view**: outlines, track IDs, predicted output, confidence, gaps, Rework flags, send countdowns, latency, fps and drops.
- **Calibration**: printable target and scale/distortion solve; focus assist with a target range; exposure panel with histogram and estimated blur; automatic region of interest; empty-lap capture for flat-field, seam template, lap length and belt speed.
- **Grading**: as specified in the brief, with two additions I recommend:
  - a "several parts in this crop" label (then one identity per part), needed to score touching or stacked parts that track as one blob;
  - a "parts fed" count per run, so undetected parts and phantom detections show up as misses.
- **Label anchoring**: labels attach to a part's time and belt position, not its track ID, so they survive pipeline changes; re-runs rematch by overlap and report anything unmatched.
- **Report**: metrics, confusion matrix, per-class precision and recall, latency percentiles per stage, pass or fail per target, on-seam accuracy broken out, CSV plus HTML. Re-runs all graded recordings through the current pipeline, with a fast path that re-classifies stored features only.

### Tests

- Unit: features on rendered shapes of known size (hex, ring, bolt silhouette, with blur and noise); every row of the correct-output table plus edge cases; grouping; scheduler against a fake clock.
- Replay regression: golden decision logs for short recordings; `sorter replay --compare` for the Mac-versus-Dell check.
- `sorter bench`: full-speed replay with per-stage timing.

## Milestones

Each "now" milestone ends with a checkpoint where the user runs a short checklist at the rig.

**M0. Camera spike.** Confirm frame rate and resolution; set a fixed exposure and gain on macOS through UVC control requests; pick the Python capture path by measured fps and timestamp quality; check for mirroring; check there is enough light at 0.5 to 1 ms on the still belt.

**M1. Capture, record/replay, live view, calibration.** Repo scaffold, config, clock, logging; live, replay and synthetic sources; recorder; Qt live view; calibration tools.
*Checkpoint 1:* print the target, calibrate scale and focus, set exposure; run the empty belt for three laps; record two or three runs of the 30-part set, including some parts placed on the seam.

**M2. Segmentation, tracking, measurement, grading tool.** Includes seam handling and the decision log.
*Checkpoint 2:* grade the recorded runs (about 90 parts). I review feature spreads, on-seam cases and bolt-head outlines, and recommend lighting changes if needed.

**M3. Classification, grouping, decisions, scheduler, report.** Parts library and `derive-ranges`, on-screen scheduled outputs, mock serial, benchmark, regression tests, README, design note.
*Checkpoint 3:* ten graded runs (at least 300 parts plus out-of-scope items); tune the confidence threshold to hold contamination under 1%. Replay and benchmark also run on the Dell under any OS, since replay needs no camera backend.

**M4 (later).** Serial protocol and adapter, timing calibration (drop-time sweep), end-to-end sorting on the Mac.

**M5 (later).** Linux on the Dell, V4L2 camera backend, calibration there, acceptance test.

## Main risks

| Risk | Plan |
| --- | --- |
| Fixed exposure on macOS | Resolved in milestone 0: works over UVC control requests |
| Enough light at 1 ms or less | Measured low in milestone 0; lighting needs to be increased |
| Lighting flicker (found in milestone 0) | Deferred by the user (2026-11-17): lighting gets fixed in hardware later; the software assumes steady lighting and does not compensate for flicker |
| Bolt-head facets as bright as the belt | Edge-plus-deviation segmentation; lighting recommendation at checkpoint 2 if outlines still break |
| Parts on the seam (2 to 3% of parts against a 5% error budget) | Seam template subtraction in M2; on-seam accuracy reported separately |
| Scheduler jitter under the interpreter lock | Measured in M3; separate process if needed |
| Recording size | Resolved in milestone 1: about 1 GB per minute |

## Verification

- `uv run pytest` passes (unit and replay regression).
- `sorter replay <recording>` reproduces the golden decision log; the same recording replayed on the Dell matches the Mac's decisions.
- `sorter bench <recording>` on the Dell reports per-stage latency with p99 under 250 ms and mean under 8.3 ms per frame (or a deliberate frame subset).
- `sorter report` over the ten graded runs shows accuracy of at least 95%, contamination of at most 1%, zero unexplained frame drops and zero send-deadline misses.
- Live run on the Mac with the debug view: every part gets one ID, one decision and one logged output with its scheduled send time; the seam never produces an output.

## Needed from the user along the way

- How the lights are powered or dimmed, and more light on the belt.
- A rough collector lead time (a placeholder is used until then).
- A printer for the calibration target and a ruler or calipers to confirm its printed scale.
- The belt running at checkpoints 1 to 3, and about 10 GB of free disk for recordings.
