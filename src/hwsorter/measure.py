"""`sorter measure`: per-part measurement diagnostic for a STATIONARY (or slow) belt.

Runs the pipeline's own stages on real frames (Preprocessor -> segment -> SeamHandler ->
features.extract -> fusion.fuse -> RangeClassifier) but without the tracker, grouper or
scheduler. Blobs are clustered across frames by their centroid in inches (within
``track.gate_in``) into parts, numbered left to right. Per part it reports the fused feature
medians against the parts-library ranges, and saves ``measure.json``, ``overlay.png`` and
``mask.png``.

Dimmed frames (PWM lighting flicker) are skipped by default: the belt brightness of each frame
is ``capture.frame_level``; frames below 0.9 x the 90th percentile of the session are dropped.
No Qt here.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .calibration import Calibration, PlaneMapper, RoiCalibration
from .capture import frame_level
from .classify import RangeClassifier, load_library
from .config import SorterConfig, load_config, project_root
from .contracts import Blob, FeatureVector, Observation, SeamObs, Track, TrackFlag
from .frames import EndOfStream, Frame
from .pipeline import provisional_scale, synthetic_calibration
from .vision.features import extract
from .vision.fusion import fuse
from .vision.seam import SeamHandler
from .vision.segment import Preprocessor, segment

log = logging.getLogger(__name__)

DIM_FRACTION = 0.9  # a frame below this x the session's 90th-percentile level is "dimmed"
MIN_SEEN_FRACTION = 0.3  # a cluster seen in fewer of the used frames is ignored as sporadic
_SHIFT = 4  # cv2 fixed-point bits for sub-pixel polylines
_PX_TOL = 1e-9

# Still synthetic scene: (kind, centre x in from the left image edge, y in from the belt top).
STILL_LAYOUT = (
    ("nut_025", 0.40, 1.50),
    ("bolt_025", 0.90, 0.50),
    ("nut_0375", 1.15, 1.50),
    ("washer_025", 1.95, 1.50),
    ("bolt_0375", 2.35, 0.50),
    ("washer_0375", 2.85, 1.50),
)


# --------------------------------------------------------------------------- sources


def still_synthetic_frames(
    cfg: SorterConfig, n_frames: int, layout=STILL_LAYOUT, flicker_depth=0.0, flicker_every=20
):
    """Frames of a stationary synthetic belt: one scene instant re-rendered (fresh noise and,
    optionally, flicker each frame). Returns (frames, scene) so the caller can calibrate."""
    from .synthetic import PartSpec, SceneConfig, SyntheticRenderer

    scene = SceneConfig(
        width=cfg.camera.width,
        height=cfg.camera.height,
        fps=cfg.camera.fps,
        speed_in_s=cfg.belt.speed_in_s,
        loop_length_in=cfg.belt.loop_length_in,
        flicker_depth=flicker_depth,
        flicker_every=flicker_every,
    )
    t_scene = 1.0
    v = scene.speed_in_s
    parts = [PartSpec(kind, t_scene - x_in / v, y_in, 0.0) for kind, x_in, y_in in layout]
    renderer = SyntheticRenderer(scene, parts)
    step = round(1e9 / scene.fps)
    frames = [
        Frame(i, 10**9 + i * step, 10**9 + i * step, renderer.render(t_scene, i))
        for i in range(n_frames)
    ]
    return frames, scene


def _grab_camera(cfg: SorterConfig, seconds: float, max_frames: int) -> list[Frame]:
    from .session import Session

    session = Session(cfg, "camera")
    session.start()
    frames: list[Frame] = []
    try:
        time.sleep(0.5)  # let the stream settle after the exposure change
        queue = session.capture.subscribe("measure", max(max_frames, cfg.pipeline.queue_frames))
        end = time.monotonic() + seconds
        print(f"capturing {seconds:g} s from the camera ...")
        while time.monotonic() < end and len(frames) < max_frames:
            try:
                frame = queue.get(timeout=0.5)
            except EndOfStream:
                break
            if frame is not None:
                frames.append(frame)
        session.capture.unsubscribe(queue)
    finally:
        session.stop()
    return frames


def _grab_recording(cfg: SorterConfig, path: str, seconds: float, max_frames: int) -> list[Frame]:
    from .recording import ReplaySource

    source = ReplaySource(path, realtime=False)
    source.start()
    frames: list[Frame] = []
    try:
        while len(frames) < max_frames:
            frame = source.read()
            if frame is None:
                continue
            if frames and (frame.t_capture_ns - frames[0].t_capture_ns) / 1e9 > seconds:
                break
            frames.append(frame)
    except EndOfStream:
        pass
    finally:
        source.stop()
    return frames


# --------------------------------------------------------------------------- calibration


@dataclass
class Rig:
    mapper: PlaneMapper
    px_per_in: float
    roi: RoiCalibration
    flat: np.ndarray | None
    provisional: bool  # scale is not a real calibration


def _rig_from_calibration(
    cal: Calibration, width: int, height: int, flat: np.ndarray | None = None
) -> Rig:
    from .calibration.store import calibration_path
    from .synthetic import SceneConfig

    scale = cal.scale
    provisional = scale is None
    if scale is None:
        scale = provisional_scale(width, height, SceneConfig().px_per_in)
    roi = cal.roi or RoiCalibration(top=0, bottom=height, left=0, right=width, source="auto")
    if flat is None and cal.flatfield_file:
        path = calibration_path(cal.machine).parent / cal.flatfield_file
        if path.is_file():
            flat = np.load(path)
    return Rig(PlaneMapper(scale), float(scale.px_per_in), roi, flat, provisional)


# --------------------------------------------------------------------------- measuring


@dataclass
class _Sighting:
    frame_idx: int
    blob: Blob
    obs: Observation
    vec: FeatureVector


@dataclass
class _Cluster:
    cx: float
    cy: float
    sightings: list[_Sighting] = field(default_factory=list)
    frames: set[int] = field(default_factory=set)

    def add(self, s: _Sighting, cx: float, cy: float) -> None:
        n = len(self.sightings)
        self.cx, self.cy = (self.cx * n + cx) / (n + 1), (self.cy * n + cy) / (n + 1)
        self.sightings.append(s)
        self.frames.add(s.frame_idx)


def frame_levels(frames: list[Frame]) -> np.ndarray:
    return np.array([frame_level(f.image) for f in frames], np.float64)


def select_frames(levels: np.ndarray, include_dimmed: bool) -> np.ndarray:
    """Indices of the frames to use: all, or those at or above 0.9 x the 90th percentile."""
    if include_dimmed or len(levels) == 0:
        return np.arange(len(levels))
    return np.flatnonzero(levels >= DIM_FRACTION * float(np.percentile(levels, 90)))


def _unit(name: str) -> str:
    return "in²" if name.endswith("_in2") else "in" if name.endswith("_in") else ""


def _num(v: float) -> float | None:
    return float(v) if math.isfinite(v) else None


def _feature_rows(
    ranges: dict[str, tuple[float, float]], median: dict[str, float], spread: dict[str, float]
) -> list[dict[str, Any]]:
    rows = []
    for name, (lo, hi) in ranges.items():
        v = median.get(name, math.nan)
        if not math.isfinite(v):
            mark, outside, score = "NAN", None, math.inf
        else:
            mark = "LOW" if v < lo else "HIGH" if v > hi else "OK"
            outside = lo - v if v < lo else v - hi if v > hi else 0.0
            score = abs(v - (lo + hi) / 2.0) / ((hi - lo) / 2.0)
        rows.append(
            {
                "feature": name,
                "unit": _unit(name),
                "median": _num(v),
                "mad": _num(spread.get(name, math.nan)),
                "lo": lo,
                "hi": hi,
                "mark": mark,
                "outside": outside,
                "score": _num(score),
            }
        )
    return rows


def _rank(library, median: dict[str, float]) -> list[dict[str, Any]]:
    """Every (class, pose) ordered best first: fewest missing features, then worst-feature score
    over the features that exist (the classifier itself scores a missing feature as inf)."""
    out = []
    for pc, spec in library.parts.items():
        for pose, ranges in spec.poses.items():
            worst, worst_name, missing = 0.0, None, []
            for name, (lo, hi) in ranges.items():
                v = median.get(name, math.nan)
                if not math.isfinite(v):
                    missing.append(name)
                    continue
                s = abs(v - (lo + hi) / 2.0) / ((hi - lo) / 2.0)
                if worst_name is None or s > worst:
                    worst, worst_name = s, name
            out.append(
                {
                    "part_class": pc.value,
                    "pose": pose,
                    "score": worst,
                    "worst_feature": worst_name or (missing[0] if missing else None),
                    "missing": missing,
                    "ranges": ranges,
                }
            )
    out.sort(key=lambda r: (len(r["missing"]), r["score"]))
    return out


def run_measurement(
    frames: list[Frame],
    cfg: SorterConfig,
    rig: Rig,
    *,
    include_dimmed: bool = False,
    library_path: str | Path | None = None,
) -> dict[str, Any]:
    """Process the frames and return the full result dict plus the overlay inputs under
    the private keys ``_overlay`` (not JSON)."""
    if not frames:
        raise ValueError("no frames were captured")
    levels = frame_levels(frames)
    used = select_frames(levels, include_dimmed)
    pre = Preprocessor(rig.flat, rig.roi, cfg.segment)
    seam = SeamHandler(cfg.seam)
    seam.set_scale(rig.px_per_in)

    clusters: list[_Cluster] = []
    per_frame: dict[int, tuple[Frame, list[tuple[Blob, _Cluster]], SeamObs | None]] = {}
    for idx in used:
        frame = frames[int(idx)]
        prepared = pre.apply(frame)
        blobs = segment(prepared, cfg.segment, rig.mapper)
        blobs, seam_obs = seam.process(prepared, blobs)
        assigned = []
        for blob in sorted(blobs, key=lambda b: -b.area_px):  # biggest first
            c_in = rig.mapper.px_to_in(blob.contour_px)
            x0, y0 = c_in.min(axis=0)
            x1, y1 = c_in.max(axis=0)
            cx, cy = float((x0 + x1) / 2), float((y0 + y1) / 2)
            valid = not blob.touches_edge
            obs = Observation(frame.seq, frame.t_capture_ns, blob, c_in, valid)
            vec = extract(obs, prepared, cfg.features)
            best, best_d = None, cfg.track.gate_in
            for cl in clusters:
                d = math.hypot(cl.cx - cx, cl.cy - cy)
                if d < best_d and int(idx) not in cl.frames:  # one blob per part per frame
                    best, best_d = cl, d
            if best is None:
                best = _Cluster(cx, cy)
                clusters.append(best)
            best.add(_Sighting(int(idx), blob, obs, vec), cx, cy)
            assigned.append((blob, best))
        per_frame[int(idx)] = (frame, assigned, seam_obs)

    n_used = len(used)
    need = max(1, math.ceil(MIN_SEEN_FRACTION * n_used))
    kept = sorted((c for c in clusters if len(c.frames) >= need), key=lambda c: c.cx)
    ignored = len(clusters) - len(kept)

    lib_path = Path(library_path or cfg.classify.library_path)
    library = load_library(lib_path if lib_path.is_absolute() else project_root() / lib_path)
    classifier = RangeClassifier(library, cfg.classify)

    parts: list[dict[str, Any]] = []
    for pid, cl in enumerate(kept, start=1):
        obs_list = [s.obs for s in cl.sightings]
        vectors = [s.vec for s in cl.sightings]
        flags = TrackFlag(0)
        if any(s.blob.touches_edge for s in cl.sightings):
            flags |= TrackFlag.TOUCHES_EDGE
        if any(s.blob.on_seam for s in cl.sightings):
            flags |= TrackFlag.ON_SEAM
        n_valid = sum(v.valid for v in vectors)
        if n_valid < cfg.track.min_frames:
            flags |= TrackFlag.FEW_FRAMES
        track = Track(pid, obs_list, flags, finished=True)
        fused = fuse(track, vectors, cfg.features)
        cls = classifier.classify(fused, flags)
        ranking = _rank(library, fused.median)
        best = ranking[0]
        runner = next((r for r in ranking if r["part_class"] != best["part_class"]), None)
        rows = _feature_rows(best["ranges"], fused.median, fused.spread)
        areas = [s.blob.area_px for s in cl.sightings]
        parts.append(
            {
                "id": pid,
                "centre_in": [round(cl.cx, 4), round(cl.cy, 4)],
                "n_frames_seen": len(cl.frames),
                "n_frames_valid": n_valid,
                "edge": bool(flags & TrackFlag.TOUCHES_EDGE),
                "on_seam": bool(flags & TrackFlag.ON_SEAM),
                "verdict": cls.verdict.name,
                "classifier_part_class": cls.part_class.value if cls.part_class else None,
                "best_class": best["part_class"],
                "best_pose": best["pose"],
                "score": _num(best["score"]),
                "missing_features": best["missing"],
                "runner_up_class": runner["part_class"] if runner else None,
                "runner_up_score": _num(runner["score"]) if runner else None,
                "worst_feature": best["worst_feature"],
                "mean_area_px": float(np.mean(areas)),
                "features": rows,
                "median": {k: _num(v) for k, v in fused.median.items()},
                "mad": {k: _num(v) for k, v in fused.spread.items()},
            }
        )

    # Representative frame: the used frame that shows the most kept parts, then the brightest.
    kept_ids = {id(c): i + 1 for i, c in enumerate(kept)}
    rep = max(
        per_frame,
        key=lambda i: (sum(id(c) in kept_ids for _, c in per_frame[i][1]), levels[i]),
    )
    frame, assigned, seam_obs = per_frame[rep]
    overlay_items = [(b, kept_ids[id(c)]) for b, c in assigned if id(c) in kept_ids]

    lo_hi = (float(levels.min()), float(levels.max()))
    result: dict[str, Any] = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "scale_provisional": rig.provisional,
        "px_per_in": rig.px_per_in,
        "frames": {
            "captured": len(frames),
            "used": n_used,
            "skipped_dimmed": len(frames) - n_used,
            "include_dimmed": include_dimmed,
            "level_min": lo_hi[0],
            "level_max": lo_hi[1],
            "level_p90": float(np.percentile(levels, 90)),
            "dim_threshold": None
            if include_dimmed
            else DIM_FRACTION * float(np.percentile(levels, 90)),
        },
        "ignored_sporadic_blobs": ignored,
        "seam_detected": bool(seam_obs is not None and seam_obs.present),
        "overlay_frame_seq": int(frame.seq),
        "library": str(lib_path),
        "parts": parts,
    }
    result["_overlay"] = (frame, overlay_items, seam_obs, parts)
    return result


# --------------------------------------------------------------------------- output


def _fmt(v: float | None, spec: str = ".4f") -> str:
    return "nan" if v is None else format(v, spec)


def format_report(result: dict[str, Any]) -> str:
    fr = result["frames"]
    lines = []
    if result["scale_provisional"]:
        lines.append(
            "WARNING: the scale is NOT calibrated; inches are PROVISIONAL "
            f"({result['px_per_in']:.0f} px/in assumed). Run the Scale tab in `sorter gui`."
        )
    dim = "dimmed frames INCLUDED" if fr["include_dimmed"] else "dimmed frames skipped"
    lines.append(
        f"frames: {fr['used']} used, {fr['skipped_dimmed']} skipped, {fr['captured']} captured "
        f"({dim}; belt level {fr['level_min']:.0f}-{fr['level_max']:.0f}, p90 {fr['level_p90']:.0f})"
    )
    if result["ignored_sporadic_blobs"]:
        lines.append(
            f"ignored {result['ignored_sporadic_blobs']} sporadic blob(s) seen in few frames"
        )
    lines.append(f"seam detected: {result['seam_detected']}   library: {result['library']}")
    lines.append("")
    head = (
        f"{'id':>2} {'centre (in)':>13} {'seen/valid':>10} {'verdict':<12} {'best class':<18} "
        f"{'score':>7} {'runner-up':<18} {'worst feature':<17} flags"
    )
    lines.append(head)
    lines.append("-" * len(head))
    for p in result["parts"]:
        runner = (
            f"{p['runner_up_class']} {_fmt(p['runner_up_score'], '.2f')}"
            if p["runner_up_class"]
            else "-"
        )
        flags = " ".join(f for f, on in (("EDGE", p["edge"]), ("SEAM", p["on_seam"])) if on)
        best = f"{p['best_class']}/{p['best_pose']}"
        lines.append(
            f"{p['id']:>2} {p['centre_in'][0]:>6.2f},{p['centre_in'][1]:>6.2f} "
            f"{p['n_frames_seen']:>4}/{p['n_frames_valid']:<5} {p['verdict']:<12} {best:<18} "
            f"{_fmt(p['score'], '.2f'):>7} {runner:<18} {p['worst_feature'] or '-':<17} {flags}"
        )
    for p in result["parts"]:
        lines.append("")
        lines.append(
            f"part {p['id']}: best {p['best_class']} / {p['best_pose']}, verdict {p['verdict']}"
            + (
                "  (EDGE: touches the image edge, no valid frames)"
                if p["edge"] and not p["n_frames_valid"]
                else ""
            )
        )
        lines.append(
            f"  {'feature':<18} {'median':>9} {'MAD':>8}   {'library range':<19} {'mark':<5} outside by"
        )
        for r in p["features"]:
            outside = (
                "" if r["outside"] in (None, 0.0) else f"{r['outside']:.4f} {r['unit']}".rstrip()
            )
            lines.append(
                f"  {r['feature']:<18} {_fmt(r['median']):>9} {_fmt(r['mad']):>8}   "
                f"[{r['lo']:.4f}, {r['hi']:.4f}]".ljust(54)
                + f" {r['mark']:<5} {outside}"
            )
    return "\n".join(lines)


def draw_overlay(
    frame: Frame, items: list[tuple[Blob, int]], seam_obs: SeamObs | None, parts: list[dict]
) -> tuple[np.ndarray, np.ndarray]:
    """(overlay BGR image, mask) for one frame. The mask is the filled blob contours."""
    h, w = frame.image.shape
    img = cv2.cvtColor(frame.image, cv2.COLOR_GRAY2BGR)
    mask = np.zeros((h, w), np.uint8)
    by_id = {p["id"]: p for p in parts}
    scale = max(0.6, w / 1600.0)
    if seam_obs is not None and seam_obs.present:
        band = np.round(np.asarray(seam_obs.band_px, np.float64) * (1 << _SHIFT)).astype(np.int32)
        cv2.polylines(img, [band.reshape(-1, 1, 2)], True, (255, 160, 0), 1, cv2.LINE_AA, _SHIFT)
        cv2.putText(
            img,
            "seam",
            tuple(int(v) for v in band[:, 0].reshape(-1, 2)[0] >> _SHIFT),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            (255, 160, 0),
            1,
            cv2.LINE_AA,
        )
    for blob, pid in items:
        pts = np.round(np.asarray(blob.contour_px, np.float64) * (1 << _SHIFT)).astype(np.int32)
        cv2.fillPoly(mask, [pts.reshape(-1, 1, 2)], 255, cv2.LINE_8, _SHIFT)
        p = by_id[pid]
        color = (0, 200, 0) if p["verdict"] == "CONFIDENT" else (0, 165, 255)
        if p["edge"]:
            color = (0, 0, 255)
        cv2.polylines(img, [pts.reshape(-1, 1, 2)], True, color, 1, cv2.LINE_AA, _SHIFT)
        x0, y0, _x1, _ = blob.mask_bbox_px
        label = f"#{pid} {p['best_class']} {p['verdict']}" + (" EDGE" if p["edge"] else "")
        org = (max(int(x0), 2), max(int(y0) - 6, 14))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):  # same-thickness dark halo
            cv2.putText(
                img,
                label,
                (org[0] + dx, org[1] + dy),
                cv2.FONT_HERSHEY_SIMPLEX,
                scale,
                (0, 0, 0),
                1,
                cv2.LINE_AA,
            )
        cv2.putText(img, label, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)
    return img, mask


def write_outputs(result: dict[str, Any], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frame, items, seam_obs, parts = result.pop("_overlay")
    overlay, mask = draw_overlay(frame, items, seam_obs, parts)
    text = format_report(result)
    paths = {
        "measure.json": json.dumps(result, indent=2, allow_nan=False),
        "measure.txt": text + "\n",
    }
    written = []
    for name, content in paths.items():
        (out_dir / name).write_text(content, encoding="utf-8")
        written.append(out_dir / name)
    cv2.imwrite(str(out_dir / "overlay.png"), overlay)
    cv2.imwrite(str(out_dir / "mask.png"), mask)
    return [*written, out_dir / "overlay.png", out_dir / "mask.png"]


# --------------------------------------------------------------------------- CLI


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "measure",
        help="measure each part on a stationary belt and compare with the library ranges",
    )
    p.add_argument("source", help='"camera", "synthetic", or a recording name or path')
    p.add_argument("--seconds", type=float, default=3.0, help="how long to capture (default 3)")
    p.add_argument("--max-frames", type=int, default=200, help="frame cap (default 200)")
    p.add_argument("--out", help="output folder (default sessions/measure-<timestamp>)")
    p.add_argument(
        "--include-dimmed", action="store_true", help="do not skip flicker-dimmed frames"
    )
    p.set_defaults(func=cmd_measure)


def cmd_measure(args: argparse.Namespace) -> int:
    from .cli import resolve_recording

    cfg = load_config(args.config)
    spec = args.source
    if args.max_frames < 1:
        raise ValueError("--max-frames must be at least 1")
    n_still = min(args.max_frames, max(1, round(args.seconds * cfg.camera.fps)))
    if spec == "synthetic":
        frames, scene = still_synthetic_frames(cfg, n_still)
        cal, flat = synthetic_calibration(scene)
        rig = _rig_from_calibration(cal, scene.width, scene.height, flat)
        rig.provisional = False  # the synthetic rig's scale is exact
    elif spec == "camera":
        frames = _grab_camera(cfg, args.seconds, args.max_frames)
        from .calibration import load_calibration

        h, w = frames[0].image.shape if frames else (cfg.camera.height, cfg.camera.width)
        rig = _rig_from_calibration(load_calibration(), w, h)
    else:
        path = str(resolve_recording(cfg, spec))
        frames = _grab_recording(cfg, path, args.seconds, args.max_frames)
        h, w = frames[0].image.shape if frames else (cfg.camera.height, cfg.camera.width)
        meta = json.loads((Path(path) / "meta.json").read_text(encoding="utf-8"))
        if meta.get("source") == "synthetic":
            from .synthetic import SceneConfig

            cal, flat = synthetic_calibration(SceneConfig(width=w, height=h))
            rig = _rig_from_calibration(cal, w, h, flat)
        else:
            from .calibration import load_calibration

            rig = _rig_from_calibration(load_calibration(), w, h)
    out_dir = (
        Path(args.out)
        if args.out
        else project_root() / "sessions" / datetime.now().strftime("measure-%Y%m%d-%H%M%S")
    )
    result = run_measurement(frames, cfg, rig, include_dimmed=args.include_dimmed)
    text = format_report(result)
    print(text)
    written = write_outputs(result, out_dir)
    print()
    for path in written:
        print(f"wrote {path}")
    return 0
