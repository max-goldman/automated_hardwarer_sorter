# Hardware Sorter Vision Program

Vision and control program for the prototype fastener sorter. A camera looks down at a
moving belt, the program finds and classifies parts, and tells the sorter where each one
goes. See `descriptions/Hardware Sorter Vision Program Project Brief.md` for the
requirements.

Status: milestone 1 (camera capture, lossless recording and replay, calibration tools, GUI).
The vision pipeline arrives in milestones 2 and 3.

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
uv run sorter gui --source my-recording    # a recording name or path
uv run sorter record empty-belt --seconds 10 --notes "empty moving belt"
uv run sorter record test --source synthetic --seconds 2
uv run sorter replay empty-belt            # add --realtime to pace playback
uv run sorter recordings
uv run sorter calibrate target --out calibration-target.pdf
uv run sorter calibrate show
uv run sorter calibrate flatfield empty-belt --max-frames 80
```

`record` options: `--seconds`, `--exposure-ms`, `--notes`, `--source camera|synthetic`.
`record` exits 1 if any frames were lost.

### Typical calibration workflow

1. `sorter calibrate target`, print at 100% ("Actual size"), check the scale bar with calipers.
2. `sorter gui`: use the Exposure, Focus, Scale and Belt tabs. Results are saved per machine.
3. Record an empty moving belt (`sorter record empty-belt --seconds 10`), then
   `sorter calibrate flatfield empty-belt`.
4. `sorter calibrate show` lists what is calibrated and what is missing.

### Milestones 2 and 3 commands

<!-- TODO (milestones 2 and 3): document the vision pipeline commands here (run, grading,
report, bench, derive-ranges) as they are added. -->

Not yet available.

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
