# Hardware Sorter Vision Program: Project Brief

ShNov 16, 2026 · @max

## Purpose

You are building the vision and control program for a prototype fastener sorter. It finds each part on a conveyor in a camera feed and decides which bin it belongs in; later it will also tell an Arduino when to turn the bin collector. Targets: at least 95% of parts given the correct output, at most 1% bin contamination, and at most 250 ms to identify each part, on a Windows or Linux laptop, developed on a Mac.

**First session scope:** everything except serial output. Show each part's output on screen on the Mac and log it with the time it would have been sent. The Arduino currently only turns the collector and has no communication yet; the serial link comes in a later session.

How to read this brief:

- **Must** items are requirements. **Suggested** items are guidance: take a better route if you see one, and say why.
- Future considerations shape the design but must not be built now.
- Open items are facts not yet confirmed. Make each one a config value and ask the user before relying on a guess.
- The six sample images are embedded under Parts in scope; the original files come with this brief.
- Before writing code, read the whole brief, ask your questions, then propose an architecture and milestone plan for the user to approve.

## System overview

Parts travel in a straight line past one camera. The laptop identifies each part and times a serial message so the rotary collector is in position when that part falls.

1. A vibratory feeder drops mixed hardware onto a feed conveyor running at about 1.33 in/s.
2. The feed conveyor hands parts to a white imaging conveyor running at 4 in/s. The 3x speed-up spreads parts apart; singulation is intended but not guaranteed.
3. The camera views about 2 x 3.3 in of the white belt: the full 2 in belt width and about 3.3 in along its length. Parts move left to right in the image.
4. Parts run off the end of the white belt into a rotary bin collector, which turns to put the right bin under the drop.
5. An Arduino with existing code drives the collector. Today it only turns the collector; a later session adds one USB serial message per part from the laptop.

**Scope of this program:** capture, detection, tracking, identification, the bin decision, timed serial output, and the supporting tools below. Motors, feeder and collector logic are out of scope.

&#91;embedded content: sorter layout · parts path on top, data path below\]

Only the highlighted box is new work; the feeder, conveyors, collector and Arduino code already exist.

## Camera and imaging

One fixed mono global-shutter camera sees each part for roughly 60 to 85 frames. Use several frames per part, and calibrate scale and lens distortion rather than hard-coding them.

| Item | Value | Notes |
| --- | --- | --- |
| Camera | Innomaker U20CAM-9281M, OmniVision OV9281 sensor | 1 MP mono, global shutter, USB 2.0 UVC ([manual summary](https://manuals.plus/asin/B0CLRN9QRC)) |
| Mode | 1280 x 800 at 120 fps (per the user's spec sheet) | Retail listing says 720p at 120 fps. Enumerate the real modes on first connection. 120 fps over USB 2.0 needs MJPG, so each frame must be decoded. |
| Controls | Manual exposure; FSIN trigger input and STROBE output pins | Strobe could sync a light to exposure later. Not needed now. |
| Lens and focus | 8 mm M12 lens with IR-cut filter, replacing the stock wide-angle lens; focus set by hand | Distortion should be modest at 8 mm, but still calibrate it. Lens sits about 7.25 in (184 mm) above the belt; the sensor about 8 in. |
| Field of view | About 2.2 x 3.3 to 3.5 in; belt fills the frame height | Parts move left to right. Dark bands at top and bottom are the belt edges. The samples show the full view at working distance. |
| Scale | Estimated 360 to 390 px/in native | The samples measure about 440 px/in because Photo Booth saved them at 1440 x 960. |
| Motion | 4 in/s is about 1,500 px/s, or about 13 px between frames | Belt speed is held steady. Each 1 ms of exposure smears a part by about 1.5 px; keep exposure at or under about 0.5 to 1 ms. |
| Time in full view | About 0.5 s for a 1 in bolt, 0.6 to 0.7 s for nuts and washers | Up to about three parts can be in view at once at the target spacing. |

**Lighting:** the samples use the intended lighting on the real belt. Several lights cast soft cross-shaped shadows around each part, and the white belt has small dark specks. Gray level varies by part (the 1/4 in washer images far darker than the 5/16 in one), so never use brightness as an identity feature.

**Sample image caveats:** the six samples were taken with the Mac Photo Booth app from the camera's output, with parts stationary at the intended working distance. They show the full view but were saved as 1440 x 960 RGB, not the camera's 1280 x 800 mono, and Photo Booth may have mirrored them. Focus may be slightly soft. Use them to understand appearance, not for calibration or tuning.

## Parts in scope

The prototype sorts six part types, five of each, so one test run is 30 parts. Size alone does not separate every pair: the 1/4 in washer (0.625 OD) and the 5/16 in nut (0.577 across corners) are nearly the same outer size. Tell those two apart by outline (round versus hex) and hole.

| Part | Sample image | Nominal key dimensions (in) | Measured in sample (in) |
| --- | --- | --- | --- |
| 1/4-20 x 1 in hex bolt | bolt-0.25in-20-1in.jpg | Shank 0.250; head 0.438 across flats, 0.505 across corners, 0.156 tall; 1.00 under head | 1.26 x 0.52 bounding box |
| 5/16-18 x 1 in hex bolt | bolt-0.375in-16-1in.jpg | Shank 0.3125; head 0.500 across flats, 0.577 across corners, 0.211 tall; 1.00 under head | 1.31 x 0.58 bounding box |
| 1/4-20 hex nut | nut-0.25in-20.jpg | 0.438 across flats, 0.505 across corners, 0.311 thick (nylon-insert, measured) | 0.51 x 0.46 |
| 5/16-18 hex nut | nut-0.375in-16.jpg | 0.500 across flats, 0.577 across corners, 0.347 thick (nylon-insert, measured) | 0.58 x 0.50 |
| 1/4 in flat washer | washer-0.25in.jpg | SAE: 0.281 ID, 0.625 OD | 0.67 OD |
| 5/16 in flat washer | washer-0.375in.jpg | SAE: 0.344 ID, 0.750 OD | 0.75 OD |

Nominal values are standard inch hex and SAE washer sizes, not measurements of these parts. The sample measurements are rough bounding boxes at about 440 px/in and include blur and shadow edges. Washers are SAE pattern for this version, confirmed by the user.

**Poses:** bolts lie on their side at any angle and may roll sideways. Nuts and washers usually lie flat. A nut can land on its side and show a rectangle; handle that pose or send the part to Miscellaneous.

**Useful features (suggested):** outline area, minimum-area rectangle, round versus hex outline, hole presence and diameter, bolt shank and head width, and overall length. Thread crest spacing (about 19 px per thread for 20 TPI, 24 px for 16 TPI, at about 380 px/in) is not needed now but would separate future parts that differ only in pitch.

### Sample images

Stationary parts on the real belt under the intended lighting, captured through Photo Booth (see the caveats under Camera and imaging).

&#91;image: bolt-0.25in-20-1in.jpg: 1/4-20 x 1 in hex bolt\]

&#91;image: bolt-0.375in-16-1in.jpg: 5/16-18 x 1 in hex bolt\]

&#91;image: nut-0.25in-20.jpg: 1/4-20 hex nut\]

&#91;image: nut-0.375in-16.jpg: 5/16-18 hex nut\]

&#91;image: washer-0.25in.jpg: 1/4 in flat washer\]

&#91;image: washer-0.375in.jpg: 5/16 in flat washer\]

## Output categories and decision rules

Every physical part gets one of eight outputs: one of the six part classes, Miscellaneous, or Rework. Parts less than 1 in apart form a group that lands in one bin, so outputs are decided per group. The user defined the correct output for each kind of group:

| Group contains | Correct output |
| --- | --- |
| One in-scope part | Its class bin |
| One out-of-scope part | Miscellaneous |
| Several in-scope parts, all one class | That class's bin |
| Mixed in-scope classes, or in-scope plus out-of-scope parts | Rework |
| Only out-of-scope parts | Miscellaneous |

Every other result is a miss, scored per part. Rework is correct only for the mixed groups above, so reworking a lone part or a same-class group is a miss. **Bin contamination is the worst miss:** an in-scope part in another class's bin, or an out-of-scope part in any labeled bin. It has its own cap of 1% (see Performance requirements).

How the program decides when it can't be sure:

- **Groups:** link any two parts whose edge-to-edge gap along the direction of travel is under the rework gap (default 1.0 in, configurable). A group is every part linked this way, directly or through others. Each group is then at least the rework gap from every other part, so it can have its own bin.
- Parts side by side across the belt have a gap of zero. Touching, overlapping or stacked parts are in the same group.
- **Lone part:** its class bin only when confidently identified; otherwise Miscellaneous. A lone part never goes to Rework, so an odd part can't keep looping.
- **Group of several:** that class's bin only when every part is confidently the same class. Miscellaneous only when every part is clearly out of scope. Anything else goes to Rework, including groups with parts that can't be resolved, such as stacked parts.
- Tune the confidence threshold on graded data to keep bin contamination under its 1% cap, even at some cost in accuracy.

&#91;embedded content: output decision per group · 4 checks\]

Labeled bins need a confident match; when a group of several is unclear, Rework gives its parts a second pass.

## Performance requirements

The bar is 95% correct outputs with at most 1% bin contamination, decisions within 250 ms, and no falling behind at one part every 250 ms. All targets are measured on the Dell, not the Mac.

| Metric | Target | How it is measured |
| --- | --- | --- |
| Accuracy | At least 95% | Parts that get the correct output for their group (table in Output categories and decision rules), divided by all parts fed. Graded over at least 300 parts: 10 runs of the 30-part set plus a few out-of-scope items per run. Until serial output exists, score the program's outputs against the graded labels. |
| Bin contamination | At most 1% | In-scope parts in another class's bin plus out-of-scope parts in any labeled bin, divided by all parts fed. These also count as accuracy misses. |
| Rework rate | Reported, no target | Share of parts sent to Rework. A health check on how well the feeder separates parts. |
| Processing latency | At most 250 ms at p99; lower is better | From capture of the last frame used for a part's decision to the decision. Report p50, p95, p99 and max, per pipeline stage. |
| Throughput | One part every 250 ms, sustained | Full camera frame rate with no growing backlog. Averaging under 8.3 ms per frame keeps up with every frame at 120 fps; otherwise process a subset of frames by design. |
| Send deadline | No misses | Every decision is ready before its scheduled send time. A miss sends Miscellaneous at once, is logged, and counts as a failure. |
| Message timing | Within about 10 ms of schedule (suggested) | Applies once serial output exists. 10 ms is 0.04 in of belt travel; log scheduled and actual send times. |
| Frame drops | Zero unexplained | Report capture fps and dropped frames in every session. |

## Timing and the Arduino link

The laptop decides when each output goes out. It predicts when a group reaches the end of the belt and schedules its output a fixed lead time earlier, so the collector will be in position as the parts fall. Build the timing now and show it on screen; serial output waits for a later session.

```latex
t_{send} = t_{capture} + \frac{d_{remaining}}{v} - t_{lead}
```

- t\_capture: when the frame was exposed, taken as arrival time minus a calibrated camera latency.
- d\_remaining: distance from the part's leading edge to the end of the belt, in that frame.
- v: belt speed, held steady at about 4.0 in/s and measured during calibration. Per-part tracking can cross-check it.
- t\_lead: time the collector needs before the part falls; configurable.

**Must:**

- Take every timing value from system calibration (see Calibration tool): distance from the camera's exit edge to the end of the belt, collector lead time, camera latency and belt speed. None are known yet.
- Run the scheduler on its own thread with a monotonic high-resolution clock. Capture and processing must never delay it.
- In the first session, the scheduler drives an on-screen and logged output: each group's output and the time it would be sent.
- Put the output format in one adapter module, so adding serial never touches vision code. Provide a screen-and-log adapter now and a mock serial adapter for tests.

**Serial protocol, later session:** the Arduino code only turns the collector today and has no communication. When serial is added, propose a simple protocol (port, baud rate, one message per part or group, output codes, optional acknowledgment) for the user to implement on the Arduino. The link must reconnect after an unplug or reset and log every message with its scheduled and actual send time.

## Software requirements and architecture

One cross-platform codebase identifies parts by measured geometry against a parts table in config. There is no trained model and no training dataset for this prototype.

**Must:**

- Run on macOS on Apple Silicon (development, an M5 MacBook Pro) and on Windows or Linux (deployment, a Dell with an i7 and 16 GB RAM). Keep platform-specific code, such as camera controls and serial port names, behind small interfaces.
- Identify parts from measured features (size, shape, holes) compared with expected ranges in a parts library file. Labeled data from the grading tool is for tuning tolerances and validation, not for training.
- Store each part in the library as data: ID, display name, output code, and expected feature ranges per pose. Adding a part means adding an entry and calibrating it from examples, with no code changes.
- Keep every tunable in a versioned config file with sensible defaults: scale, thresholds, rework gap, timing values, region of interest, camera settings.
- Detect and track several parts per frame. Each physical part gets one ID, one decision and one serial message.
- Fuse measurements across frames for each part (for example, a median of features or a vote) rather than trusting one frame.
- Handle the white belt's specks, wear and shadows, and slow lighting drift. Ignore blobs below a minimum part size.
- Capture on its own thread. Never drop frames silently.
- Write structured logs of every decision with its features, confidence and stage timings.
- Ship unit tests for feature extraction and decision rules, and replay-based regression tests.
- Run headless in production, with the debug view optional.

**Suggested:**

- Python 3.11 or later with OpenCV, NumPy and pyserial. The user has no language preference; classical vision at this resolution fits the budget in Python, and it is the quickest to iterate on. Move hot loops to compiled code only if profiling demands it.
- A pipeline of swappable stages:
  1. Frame source: live camera or recording.
  2. Segment parts from the belt background.
  3. Track parts across frames.
  4. Measure features.
  5. Classify through a pluggable classifier interface, so a learned model could be dropped in later.
  6. Decide the output: class, Miscellaneous or Rework.
  7. Schedule the send.
  8. Output adapter: screen and log now, serial later.
- A GUI toolkit that can grow into the future operator interface, such as Qt or a small local web app.

## Supporting tools

All five tools below are required for the prototype. They share the main pipeline, so what the tools measure is what the sorter does.

### Record and replay

- Record raw camera frames with capture timestamps to disk, in a format that doesn't add compression artifacts.
- Replay a recording through the identical pipeline, in real time or as fast as possible, with the mock serial adapter.
- This is the main way to develop and regression-test without running parts.

### Live debug view

- Video window with each part's outline, track ID, predicted output and confidence, and key measurements.
- Show the gap to neighbors and any Rework flag, the countdown to each scheduled send, per-part latency, and fps and dropped frames.
- Toggle it on and off. Rendering must not push the pipeline below the throughput target; draw at a lower rate if needed.

### Calibration tool

- **Scale and distortion:** measure px/in and correct lens distortion from a printed target (checkerboard or dot grid) lying on the belt. Save results to config.
- **Focus assist (requested by the user):** show a live sharpness score with a target range while the user turns the lens, and say plainly whether focus is too soft. During operation, warn if part sharpness drifts below the range.
- **Exposure:** show exposure, gain, a brightness histogram and the estimated motion blur in pixels at belt speed. Recommend settings.
- **Belt speed:** measure it from tracked parts.
- **Timing:** drop distance, collector lead time and camera latency are unknown, and the user wants them found by system calibration. Suggested, once serial exists: a drop-time sweep that switches the collector between two bins at stepped offsets around each part's predicted drop; where parts start landing in the second bin reveals the true drop time. Until then, accept hand-entered estimates.
- **Region of interest:** find the belt edges automatically, with manual override.

### Grading tool (specified by the user)

This tool builds the labeled dataset and checks accuracy in one pass; the user has no labeled images yet.

1. Press Start, run hardware through the sorter, press Stop. Save every part's crop, its full frame, features, group and predicted output.
2. Step through the parts one at a time. Show the image, the predicted identity and confidence, the group's output, and a field for the part's actual identity.
3. Label identity only: one key per class, one for "out of scope", and one to exclude a part. The field defaults to the predicted identity, so confirming takes one keystroke.
4. Work out each part's correct output from the labels and the measured grouping. The grader never judges whether Rework was needed.
5. Save labels as they are entered, allow going back to fix one, and let a session resume later.

It must also work on a recording, and both with the collector live and as a dry run with no serial output.

### Accuracy and latency report

- From graded sessions, compute accuracy, bin contamination, and the Miscellaneous and Rework rates. Add a confusion matrix of actual identity (6 classes plus out of scope) against output (8 outputs), and per-class precision and recall.
- Include latency percentiles per stage, fps, dropped frames, deadline misses and send-time error.
- Mark pass or fail against every target in Performance requirements, and export it (CSV plus a readable HTML or Markdown page).
- Re-run the current classifier over all saved graded data, so every change shows its effect on accuracy at once.

## Development and testing workflow

Build and test on the Mac with the real camera and Arduino plugged in, then validate on the Dell. Live testing on the Dell is possible but inconvenient, so the move there must be quick and predictable.

- Setup on each machine is one documented step (for example, one install script plus a requirements file). Machine-specific values come from config and calibration, never from code.
- Before any live run on the Dell, replay recordings made on the Mac there and confirm the decisions match the Mac's.
- Measure performance on the Dell. The M5 is much faster, so Mac timings don't count. Provide a benchmark mode that replays at full speed and reports per-stage timing.

**Suggested milestones:**

1. Now: capture, record and replay, live view, and calibration (scale, distortion, focus, exposure, belt speed).
2. Now: segmentation, tracking and measurement, plus the grading tool, so the user can start collecting labeled runs.
3. Now: classification, grouping and decision rules, the scheduler with on-screen output, and the report. Reach the accuracy target on graded data.
4. Later: serial protocol and adapter, timing calibration, and end-to-end sorting on the Mac.
5. Later: port to the Dell, calibrate there, and run the acceptance test.

## Technical risks

The camera and the clock carry most of the risk; check the first two rows in the first session.

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| Manual camera controls on macOS | OpenCV's macOS capture backend may not set exposure or gain on UVC cameras. Windows and Linux each use different control APIs. | Verify on day one. Put camera control behind an interface with one implementation per platform. |
| Real camera modes | The retail listing says 720p at 120 fps. At 1280 x 720 the full belt width may not fit. | Enumerate modes and measure the achieved fps on both machines before building on them. |
| MJPG decode cost | 120 compressed frames per second cost CPU time before any vision work. | Decode on the capture thread and include it in the per-stage timings on the Dell. |
| Motion blur | Long exposure smears parts by about 1.5 px per ms. | Short exposure with enough light; the calibration tool reports blur. |
| Lens distortion and parallax | Taller features sit closer to the lens and look larger. With the lens about 7.25 in above the belt, the top of a 5/16 in bolt head on its side (about 0.58 in high) looks roughly 7 to 9% larger than it would at belt level; measured on the rig, nuts and bolts read about 3 to 5% large overall, flat washers within 0.5%. | Correct distortion from a calibration target. Set each part's expected ranges from real examples, not nominal sizes. |
| Parts settling after the transfer | Parts may still tumble or roll as they enter the view. | Prefer frames where a part moves at belt speed; flag parts whose outline keeps changing. |
| Send-time jitter | OS scheduling, especially on Windows, can delay a send. | Dedicated scheduler thread and high-resolution timing; log the error of every send. |
| Camera latency | Frames arrive some unknown time after exposure, which shifts every predicted drop time. | Calibrate it as a single offset and expose it in config. |
| Future near-identical parts | M6 (0.236 in) versus 1/4 in (0.250 in) is under 6% apart. | Treat measurement precision as a design driver now, even though six parts don't need it. |

## Future considerations (design for, do not build)

The next prototype adds hardware, and a later one adds an operator interface. Nothing here is built now, but the design must not block it.

- **More hardware:** new sizes, lengths and thread pitches, metric parts, other head styles, lock washers and other finishes. Keep features extensible (length and thread pitch, for example) and let the parts library grow without code changes.
- **Add new hardware from an interface:** an operator runs a few examples of a new part, labels them in the grading tool, and the system derives the part's expected ranges and adds it to the library. The parts library, grading tool and config should make this a thin layer on top.
- **Assignable collector sections:** a configurable mapping from output to bin, possibly several bins per output or several outputs per bin. Keep that mapping in one place and let the serial adapter send whatever the Arduino needs.
- **Learned classifier:** possible later, behind the same classifier interface and trained on the dataset the grading tool accumulates.
- **Higher throughput:** a faster belt or shorter cycle time. Keep belt speed, gap and timing values in config, never as constants.

## Deliverables and acceptance

The first session is accepted when a graded dry run of at least 300 parts reaches 95% accuracy with at most 1% bin contamination, within the latency target, from config alone. End-to-end sorting is accepted after serial output is added.

**Deliverables:**

- A source repository with a README covering setup on macOS, Windows and Linux and how to run each tool.
- The sorter program, headless with an optional debug view.
- Record and replay, calibration with focus assist, grading, and the accuracy and latency report.
- Config files and a parts library holding the six prototype parts.
- Unit tests, replay regression tests and a benchmark mode.
- A short design note: the approach, the features that separate each part, and the steps to add a new part.

**Acceptance test (on the Dell):**

- [ ] At least 300 parts graded in a dry run: 10 runs of the 30-part set, each with a few out-of-scope items added (for example a different-size bolt, a wing nut or a coin).
- [ ] Accuracy at least 95%, and bin contamination at most 1%.
- [ ] Processing latency at most 250 ms at p99, with no backlog at one part every 250 ms, measured on the Dell.
- [ ] The same code runs on the Mac and the Dell; only config and calibration differ.
- [ ] Later, with serial: no send-deadline misses, send-time error within tolerance, and parts land in their scheduled bins.

## Open items

These facts are not confirmed yet. Make each one a config value, and ask the user before relying on a guess.

- [ ] Drop distance, collector lead time and camera latency: to be found by system calibration.
- [ ] Serial protocol: to be designed in a later session; the Arduino has no communication yet.

Settled with the user: parts travel left to right, belt speed is held steady, the rework gap is 1 in edge to edge, same-class groups may share a bin, washers are SAE, the lens is about 7.25 in (184 mm) above the belt, accuracy follows the correct-output table, and bin contamination is capped at 1%.
