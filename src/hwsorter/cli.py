"""Command-line entry point: `sorter <command>`."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np

from .config import load_config, machine_name


def _cmd_camera_info(args: argparse.Namespace) -> int:
    from .calibration.exposure import flicker_stats
    from .camera import resolve_backend
    from .session import Session

    cfg = load_config(args.config)
    backend = resolve_backend(cfg.camera)
    print(f"backend: {backend}")
    if backend == "avfoundation":
        from .camera.avfoundation import list_modes

        print("modes advertised by the camera:")
        for mode in list_modes(cfg.camera.usb_vendor_id, cfg.camera.usb_product_id):
            print(f"  {mode['width']}x{mode['height']} {mode['format']}  fps: {', '.join(f'{r:g}' for r in mode['fps'])}")
    session = Session(cfg, "camera")
    session.start()
    try:
        if args.exposure_ms:
            session.set_exposure(args.exposure_ms)
        print(f"capturing {args.seconds:g} s at {cfg.camera.width}x{cfg.camera.height}, exposure {session.exposure_ms:.1f} ms ...")
        time.sleep(0.5)
        before = session.capture.stats()
        time.sleep(args.seconds)
        after = session.capture.stats()
        levels = session.capture.levels()[:, 1]
        flicker = flicker_stats(levels[-int(min(len(levels), args.seconds * cfg.camera.fps)):])
        print(f"  delivered fps:      {after.fps:.2f}")
        print(f"  frames:             {after.frames - before.frames}")
        print(f"  missing (gaps):     {after.missing - before.missing} unexplained ({after.expected_missing} at startup or exposure change)")
        print(f"  driver drops:       {after.source.driver_dropped - before.source.driver_dropped}")
        print(f"  queue drops:        {after.source.queue_dropped - before.source.queue_dropped}")
        print(f"  timestamps:         {session.source.info.details.get('timestamps')}")
        print(f"  belt brightness:    {flicker.plateau:.0f} of 255")
        print(f"  flicker:            {flicker.dipped_pct:.0f}% of frames dimmed, worst {flicker.swing_pct:.0f}% down")
        print("camera controls:")
        for key, value in session.control.describe().items():
            print(f"  {key}: {value}")
    finally:
        session.stop()
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    from .session import Session

    cfg = load_config(args.config)
    session = Session(cfg, args.source)
    session.start()
    try:
        if args.exposure_ms and session.source.info.live:
            session.set_exposure(args.exposure_ms)
        time.sleep(0.5)   # let the stream settle after the exposure change
        path = session.start_recording(args.name, notes=args.notes)
        print(f"recording to {path}  (Ctrl-C to stop{f', {args.seconds:g} s' if args.seconds else ''})")
        end = time.monotonic() + args.seconds if args.seconds else None
        try:
            while end is None or time.monotonic() < end:
                time.sleep(0.5)
                status = session.recording_status()
                print(f"\r  {status['frames']} frames, {status['megabytes']:.0f} MB, backlog {status['backlog']}   ", end="", flush=True)
                if session.capture.stats().ended:
                    break
        except KeyboardInterrupt:
            pass
        summary = session.stop_recording()
        print()
        print(json.dumps(summary, indent=2))
        problems = summary["not_recorded"] + summary["frames_missing_during_recording"] + summary["source_drops_during_recording"]
        return 1 if problems else 0
    finally:
        session.stop()


def _cmd_replay(args: argparse.Namespace) -> int:
    from .frames import EndOfStream
    from .session import open_source

    cfg = load_config(args.config)
    source, _ = open_source(cfg, args.recording, realtime=args.realtime)
    reader = source.reader  # type: ignore[attr-defined]
    print(f"{reader.path}: {len(reader)} frames, {reader.duration_s:.2f} s, {reader.width}x{reader.height} at {reader.fps:g} fps, "
          f"exposure {reader.meta.get('exposure_ms')} ms, complete={reader.meta.get('complete')}")
    gaps = np.diff(reader.index["t_capture_ns"]) / 1e6
    period = 1000.0 / reader.fps
    if len(gaps):
        print(f"frame spacing: median {np.median(gaps):.2f} ms, max {gaps.max():.2f} ms, gaps over 1.5 periods: {int((gaps > 1.5 * period).sum())}")
    source.start()
    start, count, level = time.perf_counter(), 0, 0.0
    try:
        while True:
            frame = source.read()
            level += float(frame.image[::16, ::16].mean())
            count += 1
    except EndOfStream:
        pass
    elapsed = time.perf_counter() - start
    print(f"read {count} frames in {elapsed:.2f} s ({count / max(elapsed, 1e-9):.0f} fps), mean gray level {level / max(count, 1):.1f}")
    print("note: the vision pipeline is added in milestone 2; for now replay verifies the recording.")
    return 0


def _cmd_recordings(args: argparse.Namespace) -> int:
    from .recording import RecordingReader
    from .session import recordings_dir

    folder = recordings_dir(load_config(args.config))
    rows = []
    for path in sorted(folder.glob("*/meta.json")):
        try:
            with RecordingReader(path.parent) as reader:
                size = (path.parent / "frames.bin").stat().st_size / 1e6
                rows.append((path.parent.name, len(reader), reader.duration_s, size, reader.meta.get("exposure_ms"), reader.meta.get("notes", "")))
        except (OSError, ValueError) as exc:
            rows.append((path.parent.name, 0, 0.0, 0.0, None, f"unreadable: {exc}"))
    if not rows:
        print(f"no recordings in {folder}")
        return 0
    print(f"{'name':<40} {'frames':>8} {'seconds':>8} {'MB':>8} {'exp ms':>7}  notes")
    for name, frames, seconds, size, exposure, notes in rows:
        print(f"{name:<40} {frames:>8} {seconds:>8.1f} {size:>8.0f} {str(exposure):>7}  {notes}")
    return 0


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from .calibration import calibration_path, load_calibration

    cfg = load_config(args.config)
    if args.action == "target":
        from .calibration.target import board_size_mm, write_target_pdf

        out = write_target_pdf(cfg.calibration_target, args.out)
        w_mm, h_mm = board_size_mm(cfg.calibration_target)
        print(f"wrote {out}")
        print(f'Print it at 100% ("Actual size"). The board is {w_mm:g} x {h_mm:g} mm; check the {cfg.calibration_target.scale_bar_mm:g} mm bar with calipers.')
        return 0
    cal = load_calibration()
    print(f"machine: {machine_name()}   file: {calibration_path()}")
    for name in ("scale", "roi", "focus", "exposure", "belt"):
        value = getattr(cal, name)
        if value is None:
            print(f"  {name:<9} not calibrated")
        elif name == "scale":
            print(f"  scale     {value.px_per_in:.1f} px/in (range {value.px_per_in_min:.1f} to {value.px_per_in_max:.1f}), "
                  f"fit error {value.reprojection_rms_px:.2f} px, {value.views} views, {value.measured_utc}")
        else:
            print(f"  {name:<9} {value.model_dump()}")
    return 0


def _cmd_gui(args: argparse.Namespace) -> int:
    from .gui.app import run

    return run(load_config(args.config), args.source)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sorter", description="Hardware sorter vision program")
    parser.add_argument("--config", help="extra config file applied over config/default.toml and config/local.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("camera-info", help="list camera modes, then measure frame rate, drops, brightness and flicker")
    p.add_argument("--seconds", type=float, default=3.0)
    p.add_argument("--exposure-ms", type=float)
    p.set_defaults(func=_cmd_camera_info)

    p = sub.add_parser("gui", help="live view and calibration tools")
    p.add_argument("--source", default="camera", help='"camera", "synthetic", or a recording name or path')
    p.set_defaults(func=_cmd_gui)

    p = sub.add_parser("record", help="record raw frames to disk (lossless)")
    p.add_argument("name", nargs="?", default="")
    p.add_argument("--seconds", type=float, help="stop after this long (default: until Ctrl-C)")
    p.add_argument("--exposure-ms", type=float)
    p.add_argument("--notes", default="")
    p.add_argument("--source", default="camera", help='"camera" or "synthetic"')
    p.set_defaults(func=_cmd_record)

    p = sub.add_parser("replay", help="read a recording back and verify it")
    p.add_argument("recording", help="recording name or path")
    p.add_argument("--realtime", action="store_true", help="pace playback to the recorded timing")
    p.set_defaults(func=_cmd_replay)

    p = sub.add_parser("recordings", help="list recordings")
    p.set_defaults(func=_cmd_recordings)

    p = sub.add_parser("calibrate", help="calibration helpers (the interactive tools are in the GUI)")
    p.add_argument("action", choices=["target", "show"], help='"target" writes the printable target PDF; "show" prints saved calibration')
    p.add_argument("--out", default="calibration-target.pdf")
    p.set_defaults(func=_cmd_calibrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - top-level: one clear line, traceback only with -v
        if args.verbose:
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
