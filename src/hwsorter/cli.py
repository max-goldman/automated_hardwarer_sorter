"""Command-line entry point: `sorter <command>`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

import numpy as np

from .config import load_config, machine_name


def resolve_recording(cfg, spec: str):
    """Find a recording: existing path, exact folder name, else newest `<timestamp>-<spec>`."""
    from pathlib import Path

    from .session import recordings_dir

    path = Path(spec)
    if path.exists():
        return path
    folder = recordings_dir(cfg)
    exact = folder / spec
    if exact.exists():
        return exact
    found = sorted(p for p in folder.glob(f"*-{spec}") if (p / "meta.json").exists())
    if found:
        return found[-1]  # timestamps sort lexically, so the last is the newest
    available = sorted(m.parent.name for m in folder.glob("*/meta.json"))
    listing = ", ".join(available) if available else "none"
    raise FileNotFoundError(f"no recording matches {spec!r} in {folder} (available: {listing})")


def _min_one(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


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
            print(
                f"  {mode['width']}x{mode['height']} {mode['format']}  fps: {', '.join(f'{r:g}' for r in mode['fps'])}"
            )
    session = Session(cfg, "camera")
    session.start()
    try:
        if args.exposure_ms:
            session.set_exposure(args.exposure_ms)
        print(
            f"capturing {args.seconds:g} s at {cfg.camera.width}x{cfg.camera.height}, exposure {session.exposure_ms:.1f} ms ..."
        )
        time.sleep(0.5)
        before = session.capture.stats()
        time.sleep(args.seconds)
        after = session.capture.stats()
        levels = session.capture.levels()[:, 1]
        flicker = flicker_stats(levels[-int(min(len(levels), args.seconds * cfg.camera.fps)) :])
        print(f"  delivered fps:      {after.fps:.2f}")
        print(f"  frames:             {after.frames - before.frames}")
        print(
            f"  missing (gaps):     {after.missing - before.missing} unexplained ({after.expected_missing} at startup or exposure change)"
        )
        print(f"  driver drops:       {after.source.driver_dropped - before.source.driver_dropped}")
        print(f"  queue drops:        {after.source.queue_dropped - before.source.queue_dropped}")
        print(f"  timestamps:         {session.source.info.details.get('timestamps')}")
        print(f"  belt brightness:    {flicker.plateau:.0f} of 255")
        print(
            f"  flicker:            {flicker.dipped_pct:.0f}% of frames dimmed, worst {flicker.swing_pct:.0f}% down"
        )
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
        time.sleep(0.5)  # let the stream settle after the exposure change
        path = session.start_recording(args.name, notes=args.notes)
        print(
            f"recording to {path}  (Ctrl-C to stop{f', {args.seconds:g} s' if args.seconds else ''})"
        )
        end = time.monotonic() + args.seconds if args.seconds else None
        try:
            while end is None or time.monotonic() < end:
                time.sleep(0.5)
                status = session.recording_status()
                print(
                    f"\r  {status['frames']} frames, {status['megabytes']:.0f} MB, backlog {status['backlog']}   ",
                    end="",
                    flush=True,
                )
                if session.capture.stats().ended:
                    break
        except KeyboardInterrupt:
            pass
        summary = session.stop_recording()
        print()
        print(json.dumps(summary, indent=2))
        problems = (
            summary["not_recorded"]
            + summary["frames_missing_during_recording"]
            + summary["source_drops_during_recording"]
        )
        return 1 if problems else 0
    finally:
        session.stop()


def _report_pipeline(pipeline, log, adapter_name: str) -> int:
    """Print a run summary; exit status 1 if any frame was dropped (a FAIL)."""
    ends = log.recent("session_end")
    end = ends[-1] if ends else {}
    print(
        f"{adapter_name}: {end.get('n_frames', 0)} frames, {end.get('n_parts', 0)} parts, "
        f"{end.get('n_groups', 0)} groups, {end.get('n_sends', 0)} sends "
        f"({end.get('n_late', 0)} late), {end.get('n_dropped', 0)} dropped"
    )
    for stage, s in pipeline.stage_timings().items():
        print(f"  {stage:10s} mean {s['mean']:7.2f} ms  p99 {s['p99']:7.2f} ms  n={s['count']}")
    if pipeline.n_dropped:
        print(f"FAIL: {pipeline.n_dropped} frames were dropped by a queue overflow")
        return 1
    return 0


def _pipeline_paths(cfg, decision_log: str | None):
    """(session folder, decision-log path) for a run; a given log path puts crops beside it."""
    from datetime import datetime
    from pathlib import Path

    from .config import project_root

    if decision_log:
        path = Path(decision_log)
        path.unlink(missing_ok=True)  # a fresh log per run
        return path.parent, path
    session = project_root() / "sessions" / datetime.now().strftime("%Y%m%d-%H%M%S")
    return session, session / cfg.pipeline.decision_log_dir / "decision_log.jsonl"


def _run_pipeline(
    cfg, spec: str, *, fast: bool, mock: bool, decision_log, seconds=None, parts=None
) -> int:
    """Assemble source -> pipeline -> adapter and run it to the end of the source.

    fast: ManualClock, the pipeline is the only consumer and blocks rather than drops.
    live clock: source -> capture service -> bounded queue -> pipeline thread."""
    from .clock import ManualClock, MonotonicClock
    from .eventlog import EventLog
    from .frames import EndOfStream
    from .output import MockSerialAdapter, ScreenLogAdapter
    from .pipeline import (
        Pipeline,
        PipelineRunner,
        build_pipeline,
        drive,
        open_run_source,
    )

    session_dir, log_path = _pipeline_paths(cfg, decision_log)
    log = EventLog(log_path)
    adapter = (
        MockSerialAdapter()
        if mock
        else ScreenLogAdapter(
            cfg.scheduler.screen_log_keep, uncalibrated=not cfg.timing.calibrated, echo=print
        )
    )
    name = "mock-serial" if mock else "screen-log"
    print(f"decision log: {log_path}")
    pipeline: Pipeline
    if fast:
        source = open_run_source(cfg, spec, realtime=False, seconds=seconds, parts=parts)
        pipeline = build_pipeline(cfg, source, ManualClock(0), adapter, log, session_dir)
        source.start()
        try:
            drive(source, pipeline)
        finally:
            source.stop()
        pipeline.close()
        log.close()
        return _report_pipeline(pipeline, log, name)

    clock = MonotonicClock(round(cfg.scheduler.spin_ms * 1e6))
    if spec == "camera":
        from .session import Session

        session = Session(cfg, "camera")
        pipeline = build_pipeline(cfg, session.source, clock, adapter, log, session_dir)
        session.start()
        session.start_pipeline(pipeline)
        capture = session.capture
    else:
        from .capture import CaptureService

        source = open_run_source(cfg, spec, realtime=True, seconds=seconds, parts=parts)
        pipeline = build_pipeline(cfg, source, clock, adapter, log, session_dir)
        capture = CaptureService(source, log)
        runner = PipelineRunner(pipeline, capture.subscribe("pipeline", cfg.pipeline.queue_frames))
        runner.start()
        capture.start()
    try:
        end = time.monotonic() + seconds if seconds and spec == "camera" else None
        while not capture.wait_ended(0.2):
            if end is not None and time.monotonic() > end:
                break
    except (KeyboardInterrupt, EndOfStream):
        pass
    wait_s = 15.0  # let queued sends fire before closing
    if spec == "camera":
        session.stop()  # stops the pipeline runner, which flushes and closes
    else:
        capture.stop()
        runner.wait_done(10.0)
        runner.stop(wait_s)
    log.close()
    return _report_pipeline(pipeline, log, name)


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    spec = args.source
    if spec not in ("camera", "synthetic"):
        spec = str(resolve_recording(cfg, spec))
    fast = args.fast or (not args.realtime and spec != "camera")
    if not args.headless:
        print("note: the live GUI overlay is task 014; running headless")
    return _run_pipeline(
        cfg,
        spec,
        fast=fast,
        mock=False,
        decision_log=args.decision_log,
        seconds=args.seconds,
        parts=args.parts,
    )


def _cmd_replay(args: argparse.Namespace) -> int:
    from .frames import EndOfStream
    from .session import open_source

    cfg = load_config(args.config)
    if args.compare:  # re-run and diff the decisions against a previous log
        from .bench import cmd_replay_compare

        return cmd_replay_compare(args, cfg, str(resolve_recording(cfg, args.recording)))
    if args.pipeline:  # the identical pipeline, MockSerialAdapter + ManualClock
        spec = str(resolve_recording(cfg, args.recording))
        return _run_pipeline(cfg, spec, fast=True, mock=True, decision_log=args.decision_log)
    source, _ = open_source(
        cfg, str(resolve_recording(cfg, args.recording)), realtime=args.realtime
    )
    reader = source.reader  # type: ignore[attr-defined]
    print(
        f"{reader.path}: {len(reader)} frames, {reader.duration_s:.2f} s, {reader.width}x{reader.height} at {reader.fps:g} fps, "
        f"exposure {reader.meta.get('exposure_ms')} ms, complete={reader.meta.get('complete')}"
    )
    gaps = np.diff(reader.index["t_capture_ns"]) / 1e6
    period = 1000.0 / reader.fps
    if len(gaps):
        print(
            f"frame spacing: median {np.median(gaps):.2f} ms, max {gaps.max():.2f} ms, gaps over 1.5 periods: {int((gaps > 1.5 * period).sum())}"
        )
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
    print(
        f"read {count} frames in {elapsed:.2f} s ({count / max(elapsed, 1e-9):.0f} fps), mean gray level {level / max(count, 1):.1f}"
    )
    print(
        "note: the vision pipeline is added in milestone 2; for now replay verifies the recording."
    )
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    from .bench import cmd_bench

    return cmd_bench(args, load_config(args.config))


def _cmd_derive_ranges(args: argparse.Namespace) -> int:
    from collections import Counter
    from pathlib import Path

    import yaml

    from .classify import derive_ranges
    from .classify.library import library_from_dict
    from .config import project_root
    from .contracts import FusedFeatures, PartClass
    from .grading import KIND_CLASS, GradingSession, LabelStore, load_session

    default_lib = project_root() / "config" / "parts_library.yaml"
    base_path = Path(args.base) if args.base else default_lib
    if args.in_place:
        target = Path(args.out) if args.out else base_path
    else:
        target = Path(args.out) if args.out else base_path.with_suffix(".derived.yaml")
        if target.exists():
            raise FileExistsError(
                f"{target} already exists; not overwriting it (pass --in-place to overwrite, "
                "or --out for a different file)"
            )
    examples: list[tuple[PartClass, FusedFeatures]] = []
    for name in args.session:
        path = Path(name)
        if not path.exists() and (project_root() / "sessions" / name).exists():
            path = project_root() / "sessions" / name
        data = load_session(path)
        graded = GradingSession(data, LabelStore(data.root / "labels.jsonl"))
        for i, part in enumerate(graded.parts):
            lab = graded.label_of(i)
            if lab is None or lab.kind != KIND_CLASS or part.raw.get("median") is None:
                continue
            median = {
                k: (float("nan") if v is None else float(v)) for k, v in part.raw["median"].items()
            }
            examples.append(
                (
                    PartClass(lab.part_class),
                    FusedFeatures(
                        median, {}, int(part.raw.get("n_frames", 0)), bool(part.raw.get("on_seam"))
                    ),
                )
            )
    if not examples:
        raise ValueError(
            "no labelled in-scope parts found in the given sessions (grade them first)"
        )
    derived = derive_ranges(examples, args.margin)
    # Classes with no examples keep their entry from the base library, so the output loads.
    base = yaml.safe_load(base_path.read_text(encoding="utf-8")) if base_path.exists() else None
    merged = {"parts": dict((base or {}).get("parts", {}))}
    merged["parts"].update(derived["parts"])
    library_from_dict(merged)  # raises if the result would not load (e.g. no base for unseen parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    header = "# Derived by `sorter derive-ranges` from graded sessions; review before using.\n"
    target.write_text(header + yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
    counts = Counter(pc for pc, _ in examples)
    print(
        f"{len(examples)} labelled parts from {len(args.session)} session(s); margin {args.margin:g}"
    )
    for pc in PartClass:
        state = "derived" if counts[pc] else "kept from base library"
        print(f"  {pc.value:<12} {counts[pc]:>3} examples  ({state})")
    print(f"wrote {target}")
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
                rows.append(
                    (
                        path.parent.name,
                        len(reader),
                        reader.duration_s,
                        size,
                        reader.meta.get("exposure_ms"),
                        reader.meta.get("notes", ""),
                    )
                )
        except (OSError, ValueError) as exc:
            rows.append((path.parent.name, 0, 0.0, 0.0, None, f"unreadable: {exc}"))
    if not rows:
        print(f"no recordings in {folder}")
        return 0
    print(f"{'name':<40} {'frames':>8} {'seconds':>8} {'MB':>8} {'exp ms':>7}  notes")
    for name, frames, seconds, size, exposure, notes in rows:
        print(f"{name:<40} {frames:>8} {seconds:>8.1f} {size:>8.0f} {exposure!s:>7}  {notes}")
    return 0


def _calibrate_flatfield(cfg, args: argparse.Namespace) -> int:
    from .calibration import calibration_path, load_calibration, save_calibration
    from .calibration.flatfield import build_flatfield
    from .recording import RecordingReader

    path = resolve_recording(cfg, args.recording)
    with RecordingReader(path) as reader:
        if len(reader) == 0:
            raise ValueError(f"recording {path} has no frames")
        step = max(1, len(reader) // args.max_frames)
        frames = [frame.image for i, frame in enumerate(reader) if i % step == 0]
    flat = build_flatfield(frames, max_frames=args.max_frames)
    cal = load_calibration()
    target = calibration_path().with_suffix(".flatfield.npy")
    target.parent.mkdir(parents=True, exist_ok=True)
    np.save(target, flat)
    cal.flatfield_file = target.name
    save_calibration(cal)
    print(
        f"flat-field from {len(frames)} frames of {path.name}: "
        f"min {flat.min():.0f}, max {flat.max():.0f}, mean {flat.mean():.0f} (gray levels)"
    )
    print(f"saved {target} and recorded it in {calibration_path()}")
    return 0


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from .calibration import calibration_path, load_calibration

    cfg = load_config(args.config)
    if args.action == "flatfield":
        return _calibrate_flatfield(cfg, args)
    if args.action == "target":
        from .calibration.target import board_size_mm, write_target_pdf

        out = write_target_pdf(cfg.calibration_target, args.out)
        w_mm, h_mm = board_size_mm(cfg.calibration_target)
        print(f"wrote {out}")
        print(
            f'Print it at 100% ("Actual size"). The board is {w_mm:g} x {h_mm:g} mm; check the {cfg.calibration_target.scale_bar_mm:g} mm bar with calipers.'
        )
        return 0
    cal = load_calibration()
    print(f"machine: {machine_name()}   file: {calibration_path()}")
    if cal.flatfield_file:
        flat = calibration_path().parent / cal.flatfield_file
        status = "ok" if flat.exists() else "MISSING (rebuild with calibrate flatfield)"
        print(f"  flatfield {flat}  [{status}]")
    else:
        print("  flatfield not calibrated")
    for name in ("scale", "roi", "focus", "exposure", "belt"):
        value = getattr(cal, name)
        if value is None:
            print(f"  {name:<9} not calibrated")
        elif name == "scale":
            print(
                f"  scale     {value.px_per_in:.1f} px/in (range {value.px_per_in_min:.1f} to {value.px_per_in_max:.1f}), "
                f"fit error {value.reprojection_rms_px:.2f} px, {value.views} views, {value.measured_utc}"
            )
        else:
            print(f"  {name:<9} {value.model_dump()}")
    return 0


def _cmd_gui(args: argparse.Namespace) -> int:
    from .gui.app import run

    cfg = load_config(args.config)
    source = args.source
    if source not in ("camera", "synthetic"):
        source = str(resolve_recording(cfg, source))
    return run(cfg, source)


def _cmd_grade(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .config import project_root
    from .gui.grading import run_grading

    path = Path(args.session)
    if not path.exists() and (project_root() / "sessions" / args.session).exists():
        path = project_root() / "sessions" / args.session
    return run_grading(path, args.labels)


def _cmd_report(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .config import project_root
    from .report import RerunError, generate

    sessions = []
    for name in args.session:
        path = Path(name)
        if not path.exists() and (project_root() / "sessions" / name).exists():
            path = project_root() / "sessions" / name
        sessions.append(path)
    cfg = load_config(args.config) if args.rerun else None
    try:
        data, csv_path, html_path = generate(sessions, args.out, rerun=args.rerun, cfg=cfg)
    except (FileNotFoundError, RerunError) as e:
        print(f"error: {e}")
        return 2
    for c in data.checks:
        print(
            f"{c.verdict:<5} {c.name}: {c.measured if c.measured is not None else 'n/a'} (target {c.target_text})"
        )
    print(f"overall: {data.overall}")
    print(f"wrote {csv_path}\nwrote {html_path}")
    return 0 if data.overall == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sorter", description="Hardware sorter vision program")
    parser.add_argument(
        "--config", help="extra config file applied over config/default.toml and config/local.toml"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "camera-info",
        help="list camera modes, then measure frame rate, drops, brightness and flicker",
    )
    p.add_argument("--seconds", type=float, default=3.0)
    p.add_argument("--exposure-ms", type=float)
    p.set_defaults(func=_cmd_camera_info)

    p = sub.add_parser("gui", help="live view and calibration tools")
    p.add_argument(
        "--source", default="camera", help='"camera", "synthetic", or a recording name or path'
    )
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
    p.add_argument(
        "--pipeline",
        action="store_true",
        help="run the full vision pipeline (MockSerialAdapter, ManualClock) instead of just reading",
    )
    p.add_argument("--decision-log", help="decision log path for --pipeline (crops go beside it)")
    p.add_argument(
        "--compare",
        metavar="DECISION_LOG",
        help="run the pipeline and diff its decisions against this log (timings ignored); "
        "exit 1 on any mismatch (the Mac-vs-Dell check)",
    )
    p.set_defaults(func=_cmd_replay)

    p = sub.add_parser(
        "bench",
        help="full-speed replay with per-stage timings vs the 8.3 ms/frame budget "
        "(dev-box numbers do not count for acceptance)",
    )
    p.add_argument("recording", nargs="?", help='recording name or path, or "synthetic"')
    p.add_argument(
        "--compare-strides",
        action="store_true",
        help="accuracy guard: frame_stride 1 vs 2 and max_feature_frames 10 vs 20 on a synthetic run",
    )
    p.add_argument("--parts", type=_min_one, default=60, help="--compare-strides: part count")
    p.set_defaults(func=_cmd_bench)

    p = sub.add_parser("run", help="run the sorting pipeline on a source")
    p.add_argument(
        "--source", default="camera", help='"camera", "synthetic", or a recording name or path'
    )
    p.add_argument("--headless", action="store_true", help="no GUI")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--realtime", action="store_true", help="live clock, paced source")
    mode.add_argument("--fast", action="store_true", help="ManualClock, as fast as possible")
    p.add_argument("--seconds", type=float, help="stop after this long (synthetic scene length)")
    p.add_argument("--parts", type=_min_one, help="synthetic: number of parts (default 10)")
    p.add_argument("--decision-log", help="decision log path (default: under sessions/)")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser(
        "grade", help="label the parts of a session (live or from replay --pipeline) for grading"
    )
    p.add_argument("session", help="session folder (or name under sessions/) or decision-log file")
    p.add_argument("--labels", help="label store path (default: <session>/labels.jsonl)")
    p.set_defaults(func=_cmd_grade)

    p = sub.add_parser(
        "report", help="accuracy and latency report (CSV + HTML) from graded sessions"
    )
    p.add_argument("session", nargs="+", help="session folder(s) (or names under sessions/)")
    p.add_argument(
        "--rerun",
        action="store_true",
        help="first re-run each graded recording through the current pipeline (full replay)",
    )
    p.add_argument("--out", help="output folder (default: the first session folder)")
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser(
        "derive-ranges", help="derive a parts-library YAML from graded sessions (never overwrites)"
    )
    p.add_argument("session", nargs="+", help="graded session folder(s) (or names under sessions/)")
    p.add_argument("--margin", type=float, default=0.1, help="range padding fraction (default 0.1)")
    p.add_argument(
        "--base", help="library to take unseen parts from (default config/parts_library.yaml)"
    )
    p.add_argument(
        "--out", help="output file (default: parts_library.derived.yaml next to the base library)"
    )
    p.add_argument("--in-place", action="store_true", help="overwrite the target if it exists")
    p.set_defaults(func=_cmd_derive_ranges)

    from .measure import register as register_measure

    register_measure(sub)  # `sorter measure` (parser and handler live in measure.py)

    p = sub.add_parser("recordings", help="list recordings")
    p.set_defaults(func=_cmd_recordings)

    p = sub.add_parser(
        "calibrate", help="calibration helpers (the interactive tools are in the GUI)"
    )
    csub = p.add_subparsers(dest="action", required=True)
    q = csub.add_parser("target", help="write the printable calibration target PDF")
    q.add_argument("--out", default="calibration-target.pdf")
    csub.add_parser("show", help="print the saved calibration")
    q = csub.add_parser("flatfield", help="build the illumination map from an empty-belt recording")
    q.add_argument("recording", help="recording name or path (empty, moving belt)")
    q.add_argument("--max-frames", type=_min_one, default=80, help="frames used for the median")
    p.set_defaults(func=_cmd_calibrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if args.verbose:
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
