# Hardware-pending work

This branch parks everything that needs the camera, the rig or a physical change. Don't schedule any of it until the user says the hardware is available. The branch holds only this file; when hardware work resumes, merge `develop` into it, or this file into `develop`.

Status at 2026-11-17: the camera works on the session-2 Mac (macOS). Session 3 runs on another machine with no camera. The user says no collector, no extra light and no moving conveyor are coming yet.

## Physical changes awaited (user)
| Item | Status | Why it matters |
| --- | --- | --- |
| Lighting flicker fix | Deferred; software assumes steady lighting | About 50% of frames dimmed (PWM-like) on 2026-11-17. Check again after the fix: `uv run sorter camera-info --seconds 3 --exposure-ms 1.0` should show near-zero dimmed frames |
| More light | Not yet | Belt reads 125/255 at 1.0 ms. 0.5 ms (0.73 px blur) needs about 2-3x the light to reach about 180 |
| Moving conveyor | Not yet | Needed for every real test with moving parts: empty-belt laps, belt speed, seam template, motion blur |
| Collector and Arduino link | Not yet | M4: serial protocol, drop-time sweep, collector lead time |

## Checkpoint 1 at the rig (needs camera, printer, belt running)
1. `uv run sorter calibrate target`; print it at 100% and measure the 50 mm scale bar.
2. `uv run sorter gui`: set the exposure; focus the lens (settle the provisional thresholds of 2.2 and 3.2 px); capture three target views and solve the scale (settle the nominal 367 px/in); save the belt edges.
3. With the belt running, record the empty belt for about 45 s (at least 3 laps), then build the flat-field: `uv run sorter calibrate flatfield <name>`. A real flat-field is REQUIRED: with an identity flat-field the whole ROI segments as one blob.
4. Record 2-3 runs of the 30-part set, including parts placed on the seam.
5. Learn the seam template and the belt speed from the empty laps (needs task 005 merged).

## Real-data checks deferred from software tasks
- 004 segmentation: on the session-2 Mac recording `recordings/20261117-002046-still-belt-1ms` (a stationary belt with ONE bolt lying in view, not empty), segmentation found exactly that bolt on the 205 steady frames. 161 dimmed frames gave 0-6 extra blobs (flicker, deferred). Repeat on a real empty-belt and moving-parts recording after checkpoint 1, with a calibrated flat-field. That recording exists only on the session-2 Mac (`recordings/` is gitignored).
- 007 features: compare synthetic feature values against real parts (parallax on bolt heads, washer-hole rim shadow, which reads about 0.035 in small on synthetic). Then `derive-ranges` from graded real runs.
- 011 pipeline: a live run on the Mac with the debug view (`sorter run`). Every part gets one ID, one decision and one logged send; the seam never produces an output.
- Checkpoint 2: grade the recorded runs (about 90 parts); review feature spreads, on-seam cases and bolt-head outlines.
- Checkpoint 3: ten graded runs (300+ parts plus out-of-scope items); tune the confidence threshold for contamination at or below 1%.

## Later milestones (from the approved plan)
- **M4:** serial protocol and adapter (reconnect after unplug or reset, log scheduled and actual send times), a drop-time sweep for timing calibration, end-to-end sorting on the Mac. Calibrate the collector lead time, camera latency, runway and belt speed (all placeholders marked "uncalibrated" today).
- **M5:** Linux on the Dell; V4L2 camera backend (placeholder in `camera/base.py`); calibration there; `sorter replay --compare` Mac vs Dell; `sorter bench` on the Dell (p99 at most 250 ms, at most 6.2 ms busy per camera frame with frame stride 2); the acceptance test.

## Camera facts (measured on the session-2 Mac)
- Sonix USB 2.0 camera (vendor 0x0c45, product 0x636d). 1280x800 at 120 fps via AVFoundation (420v) delivers 120.6 fps, 0 unexplained drops.
- Manual exposure via UVC requests (libusb): 0.2 ms minimum, 0.1 ms steps. Gain has no effect. Switching exposure mode stalls the stream about 0.2 s (logged as expected).
