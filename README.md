# Hardware Sorter Vision Program

Vision and control program for the prototype fastener sorter. A camera looks down at a
moving belt, the program finds and classifies parts, and tells the sorter where each one
goes. See `descriptions/Hardware Sorter Vision Program Project Brief.md` for the
requirements.

Status: milestones 1 to 3 (camera capture, recording and replay, calibration tools, GUI, and the
vision pipeline with grading, reporting and benchmarking). Serial output and the Linux camera
are later milestones.

## Setup

Requires Python 3.12 or 3.13, managed by [uv](https://docs.astral.sh/uv/).

- **macOS / Linux:** `scripts/setup.sh` installs uv if missing, then runs `uv sync`.
- **Windows:** install uv (`winget install astral-sh.uv`, or see the uv docs), then run
  `uv sync` in this folder.

Then run commands as `uv run sorter <command>`.

Camera support: the USB camera (Innomaker U20CAM-9281M, OV9281) works on macOS through
AVFoundation. The Linux (v4l2) camera backend is milestone 5 and is not available yet. On
every platform, `--source synthetic` and replaying recordings work without a camera.

## Commands

Global options (before the command): `--config FILE` (extra config file) and `-v` (verbose,
shows tracebacks).

| Command | What it does |
| --- | --- |
| `sorter camera-info` | Lists camera modes, then measures frame rate, drops, brightness, flicker. |
| `sorter gui` | Live view and calibration tools. |
| `sorter record` | Records raw frames to disk, lossless. |
| `sorter replay` | Reads a recording back and verifies it. |
| `sorter recordings` | Lists recordings. |
| `sorter calibrate target` | Writes the printable calibration target PDF. |
| `sorter calibrate show` | Prints the saved calibration. |
| `sorter calibrate flatfield` | Builds the illumination map from an empty-belt recording. |

Examples:

```
uv run sorter camera-info --seconds 5 --exposure-ms 0.5
uv run sorter gui                          # live camera
uv run sorter gui --source synthetic       # no camera needed
uv run sorter gui --source empty-belt     # a recording name or path
uv run sorter record empty-belt --seconds 10 --notes "empty moving belt"
uv run sorter record test --source synthetic --seconds 2
uv run sorter replay empty-belt            # short name finds the newest; add --realtime to pace playback
uv run sorter recordings
uv run sorter calibrate target --out calibration-target.pdf
uv run sorter calibrate show
uv run sorter calibrate flatfield empty-belt --max-frames 80
```

Recording arguments (`replay`, `gui --source`, `calibrate flatfield`) accept a path, an exact
folder name from `sorter recordings`, or the short name you gave `record` (folders are named
`<YYYYmmdd-HHMMSS>-<name>`; the newest match wins). Unknown names list what is available.

`record` options: `--seconds`, `--exposure-ms`, `--notes`, `--source camera|synthetic`.
`record` exits 1 if any frames were lost.

### Typical calibration workflow

1. `sorter calibrate target`, print at 100% ("Actual size"), check the scale bar with calipers.
2. `sorter gui`: use the Exposure, Focus, Scale and Belt tabs. Results are saved per machine.
3. Record an empty moving belt (`sorter record empty-belt --seconds 10`), then
   `sorter calibrate flatfield empty-belt`.
4. `sorter calibrate show` lists what is calibrated and what is missing, including the
   flat-field file and whether it exists.

### Stationary-belt mode

Live tests start with the belt stopped and parts placed by hand. `sorter run --stationary` (or
`belt.mode = "stationary"` in `config/local.toml`) switches the pipeline to that: speed is 0,
nothing is timed, and each part is decided once it has sat still long enough
(`stationary.settle_s`). The decision is printed at once (`DECISION group 3 [#3,#4] -> rework`)
and logged as a `group` record with `"stationary": true`. There are no `send` records. A hand
or arm in view (a large blob touching the image edge) is ignored and freezes decisions until it
leaves. A part that is moved is measured again as a new part; a part taken away re-decides its
group. `sorter grade` and `sorter report` work on the session as for a moving one (a superseded
group decision is graded too: they grade decisions, and `sent_output` is empty).

```
uv run sorter run --source camera --stationary --headless      # live, belt stopped
uv run sorter run --source synthetic --headless --fast --stationary   # demo: parts, a hand, a pair
```

### Milestones 2 and 3 commands

The vision pipeline: segment, seam, track, features, classify, decide, schedule. The design,
the features that separate each part pair, how to add a part, and the known limits are in
`docs/design.md`. These need no camera: use `--source synthetic` or a recording.

| Command | What it does |
| --- | --- |
| `sorter run` | Runs the pipeline on a source and writes a decision log. |
| `sorter replay <rec> --pipeline` | Runs a recording through the pipeline. |
| `sorter replay <rec> --compare <log>` | Re-runs and diffs against a log (exit 1 on mismatch); floats compare within 1e-4 (`--float-tol`), `--strict` = exact. |
| `sorter grade <session>` | Qt tool to label each detected part (ground truth). |
| `sorter report <session>...` | Accuracy and latency report (CSV + HTML) from graded sessions. |
| `sorter bench` | Per-stage timing, and the stride accuracy guard. |
| `sorter derive-ranges <session>...` | Builds a parts-library YAML from graded sessions. |
| `sorter measure <source>` | Per-part measurements on a stationary belt vs the library ranges (table, `measure.json`, overlay). |

```
uv run sorter measure camera --seconds 3   # stopped belt; writes sessions/measure-<time>/
uv run sorter run --source synthetic --headless
uv run sorter run --source synthetic --headless --parts 30 --decision-log out/log.jsonl
uv run sorter replay my-rec --pipeline --decision-log out/log.jsonl
uv run sorter replay my-rec --compare out/log.jsonl     # Mac vs Dell check; timings ignored
uv run sorter grade out                                  # label parts; saves <session>/labels.jsonl
uv run sorter report out                                 # add --rerun to re-run the pipeline first
uv run sorter report session-a session-b --out reports
uv run sorter bench my-rec
uv run sorter bench --compare-strides                    # frame_stride 1 vs 2, feature frames 10 vs 20
uv run sorter derive-ranges out
```

Options (from each command's `--help`):

- `run`: `--source camera|synthetic|<recording>` (default `camera`), `--headless`,
  `--realtime` or `--fast`, `--seconds`, `--parts` (synthetic part count), `--decision-log`,
  `--stationary` (belt stopped, parts placed by hand; see below).
- `replay`: `--realtime`, `--pipeline`, `--decision-log` (with `--pipeline`), `--compare LOG` (`--float-tol`, `--strict`).
- `grade`: `--labels` (default `<session>/labels.jsonl`). `session` is a folder, a name under
  `sessions/`, or a decision-log file.
- `report`: `--rerun`, `--out` (default: the first session folder).
- `bench`: `recording` (name, path, or `synthetic`), `--compare-strides`, `--parts`.
- `derive-ranges`: `--margin` (default 0.1), `--base` (library to take unseen parts from,
  default `config/parts_library.yaml`), `--out`, `--in-place`.

`derive-ranges` collects the fused feature vector of every part you labelled as an in-scope
class and writes ranges as one pose called `measured`. It never overwrites hand edits silently:
by default it writes `config/parts_library.derived.yaml` and refuses if that (or `--out`) already
exists; `--in-place` is required to overwrite. Classes with no labelled examples are copied from
the base library so the result still loads. Review the output before using it.

## Configuration

Settings are layered; later layers override earlier ones key by key:

1. `config/default.toml`: every tunable, committed. Do not edit per machine.
2. `config/local.toml`: your machine's overrides (untracked). Same layout, only changed keys.
3. `--config FILE`: an extra file for one run.

Unknown keys are rejected, so typos fail loudly. Values measured by calibration never go in
these files.

## Where things live

- Calibration: `config/calibration/<machine>.json` (untracked), one file per machine. The
  flat-field map is saved beside it as `<machine>.flatfield.npy` and referenced by the
  `flatfield_file` entry.
- Recordings: `recordings/<timestamp>-<name>/` (untracked; folder set by `recording.dir`),
  holding `frames.bin`, an index and `meta.json`.
- Set `HWSORTER_ROOT` to use another project folder and `HWSORTER_MACHINE` to override the
  machine name used for the calibration file.

## Tests and lint

```
uv run pytest                              # all tests, headless
uvx ruff@0.17.5 check src tests            # lint
uvx ruff@0.17.5 format --check src tests   # format check
```
