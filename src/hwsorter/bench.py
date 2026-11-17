"""Benchmark, decision-log comparison, golden scenarios and the stride-accuracy guard.

* ``run_bench``       full-speed (ManualClock) replay with per-stage timings vs the frame budget.
* ``diff_decisions``  compare two decision logs on the determinism view (the Mac-vs-Dell check).
* ``GOLDEN_SCENARIOS``  small deterministic synthetic scenes whose decision logs are committed.
* ``compare_strides`` accuracy / feature-error guard for the adopted processing budget.

Dev-box timings are informative only: acceptance numbers must come from the Dell.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .config import SorterConfig
from .contracts import Output, normalise_for_compare
from .pipeline import summarize

BUDGET_MS = 8.3  # per captured frame (120 fps)
START_NS = 1_000_000_000
# The decision-log determinism view also drops the host name (it differs per machine).
EXTRA_IGNORE_KEYS = frozenset({"machine"})

# Acceptance thresholds for the stride guard (defaults vs stride 1 / 20 frames).
MAX_ACCURACY_LOSS_PP = 0.5
MAX_FEATURE_ERROR_GROWTH_IN = 0.002

NOT_FOR_ACCEPTANCE = "dev-box numbers do not count for acceptance (that needs the Dell)"


# --------------------------------------------------------------------------- environment


def cpu_model() -> str:
    """CPU model string of this machine, for labelling benchmark numbers."""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout.strip()
            if out:
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    return platform.processor() or platform.machine() or "unknown CPU"


# --------------------------------------------------------------------------- decision logs


def read_log(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _drop_keys(obj: Any, keys: frozenset[str]) -> Any:
    if isinstance(obj, dict):
        return {k: _drop_keys(v, keys) for k, v in obj.items() if k not in keys}
    if isinstance(obj, list):
        return [_drop_keys(v, keys) for v in obj]
    return obj


def decision_view(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Determinism view of a decision log (``normalise_for_compare`` plus the host name)."""
    return _drop_keys(normalise_for_compare(records), EXTRA_IGNORE_KEYS)


def canonical_text(records: list[dict[str, Any]]) -> str:
    """The golden file format: one JSON line per record of the determinism view, kinds in
    first-seen order and file order within a kind. Byte-stable for equal views."""
    view = decision_view(records)
    lines = [
        json.dumps(rec, sort_keys=True, separators=(",", ":"))
        for kind in view
        for rec in view[kind]
    ]
    return "\n".join(lines) + "\n"


# Cross-machine tolerances. The same recording replayed on another CPU/libm/SIMD build gives the
# same decisions but floats that differ around 1e-5 (largest seen 2.1e-5), so floats compare
# with |a-b| <= abs_tol + rel_tol*|b|.
FLOAT_ABS_TOL = 1e-4
FLOAT_REL_TOL = 1e-4
# Integer ns times derived from floats (latency-corrected / scheduled) may move by this much.
NS_TOL = 1000
DERIVED_NS_KEYS = frozenset({"t_send_ns", "t_ref_ns", "t_closed_ns"})


@dataclass(frozen=True)
class CompareTol:
    """Compare rules for ``diff_decisions`` (``STRICT`` = exact equality everywhere).

    Per value, in the determinism view (timings, host name etc. already dropped):
    * bool, str, None, and every int NOT listed below (record counts, ids, ref_seq, n_frames,
      on_seam, flags, member ids, miss reasons, t_first_ns/t_last_ns taken from the recording,
      schema_version): EXACT.
    * float (feature medians/spreads, scores, inch positions), or an int compared with a float:
      |a-b| <= abs_tol + rel_tol*|b|; NaN equals NaN (null equals null via the exact rule).
    * int under key t_send_ns / t_ref_ns / t_closed_ns (derived from floats): within ns_tol ns.
    * dict keys, list lengths, types otherwise: EXACT."""

    abs_tol: float = FLOAT_ABS_TOL
    rel_tol: float = FLOAT_REL_TOL
    ns_tol: int = NS_TOL
    strict: bool = False


STRICT = CompareTol(0.0, 0.0, 0, strict=True)


def _isnum(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _diff_value(
    a: Any,
    b: Any,
    path: str,
    out: list[str],
    limit: int,
    tol: CompareTol = STRICT,
    dev: list[float] | None = None,
) -> None:
    """Append mismatches of ``a`` (expected) vs ``b`` (actual) to ``out``; ``dev[0]`` tracks the
    largest tolerated float deviation seen (for reporting)."""
    if len(out) >= limit:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: missing in expected, got {b[k]!r}")
            elif k not in b:
                out.append(f"{path}.{k}: expected {a[k]!r}, missing in actual")
            else:
                _diff_value(a[k], b[k], f"{path}.{k}", out, limit, tol, dev)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _diff_value(x, y, f"{path}[{i}]", out, limit, tol, dev)
    elif tol.strict or not (_isnum(a) and _isnum(b)):
        if a != b:
            out.append(f"{path}: expected {a!r}, got {b!r}")
    elif isinstance(a, int) and isinstance(b, int):
        key = path.rsplit(".", 1)[-1]
        if key in DERIVED_NS_KEYS:
            d = abs(a - b)
            if d > tol.ns_tol:
                out.append(f"{path}: expected {a!r}, got {b!r} (off by {d} ns)")
        elif a != b:
            out.append(f"{path}: expected {a!r}, got {b!r}")
    else:
        fa, fb = float(a), float(b)
        if fa != fa and fb != fb:  # both NaN
            return
        d = abs(fa - fb)
        if d <= tol.abs_tol + tol.rel_tol * abs(fb):
            if dev is not None and d == d:
                dev[0] = max(dev[0], d)
        else:
            out.append(f"{path}: expected {a!r}, got {b!r} (|diff|={d:.3g})")


def diff_decisions(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    limit: int = 25,
    tol: CompareTol = STRICT,
    dev: list[float] | None = None,
) -> list[str]:
    """Human-readable mismatches between two decision logs (empty list = same decisions),
    ignoring timings and the other determinism-ignored keys. Compared per record kind.
    Default is exact; pass ``CompareTol()`` for the cross-machine tolerant compare (see there),
    and a one-element list as ``dev`` to receive the largest tolerated float deviation."""
    ea, aa = decision_view(expected), decision_view(actual)
    out: list[str] = []
    for kind in sorted(set(ea) | set(aa)):
        ra, rb = ea.get(kind, []), aa.get(kind, [])
        if len(ra) != len(rb):
            out.append(f"{kind}: expected {len(ra)} records, got {len(rb)}")
        for i, (x, y) in enumerate(zip(ra, rb, strict=False)):
            _diff_value(x, y, f"{kind}[{i}]", out, limit, tol, dev)
    return out[:limit]


# --------------------------------------------------------------------------- replay driver


def replay_decisions(
    cfg: SorterConfig, spec: str, log_path: str | Path, *, bench: bool = False
) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
    """Replay ``spec`` (recording path, or "synthetic") through the full pipeline with a
    ManualClock and MockSerialAdapter, writing the decision log to ``log_path``.

    Returns (pipeline, records, timing) where ``timing`` has per-input-frame wall times of
    ``Pipeline.process`` (``all_ms``), those of processed frames only (``processed_ms``), and the
    total wall time (``wall_s``, includes reading and flushing)."""
    from .clock import ManualClock
    from .eventlog import EventLog
    from .frames import EndOfStream
    from .output import MockSerialAdapter
    from .pipeline import build_pipeline, open_run_source

    path = Path(log_path)
    path.unlink(missing_ok=True)
    log = EventLog(path)
    source = open_run_source(cfg, spec, realtime=False)
    pipeline = build_pipeline(cfg, source, ManualClock(0), MockSerialAdapter(), log, path.parent)
    all_ms: list[float] = []
    processed_ms: list[float] = []
    t_wall = time.perf_counter()
    source.start()
    try:
        while True:
            try:
                frame = source.read()
            except EndOfStream:
                break
            if frame is None:
                continue
            t0 = time.perf_counter()
            snap = pipeline.process(frame)
            dt = (time.perf_counter() - t0) * 1e3
            if bench:
                all_ms.append(dt)
                if snap.processed:
                    processed_ms.append(dt)
    finally:
        source.stop()
    t0 = time.perf_counter()
    pipeline.close()  # flush (finishes tracks, fires sends) + session_end
    flush_ms = (time.perf_counter() - t0) * 1e3
    log.close()
    wall_s = time.perf_counter() - t_wall
    timing = {
        "all_ms": all_ms,
        "processed_ms": processed_ms,
        "wall_s": wall_s,
        "flush_ms": flush_ms,
    }
    return pipeline, read_log(path), timing


# --------------------------------------------------------------------------- bench


@dataclass
class BenchResult:
    recording: str
    cpu: str
    fps: float
    n_frames: int
    n_processed: int
    n_parts: int
    wall_s: float
    per_frame: dict[str, float]  # over every input frame (skipped frames count ~0)
    per_processed: dict[str, float]
    stages: dict[str, dict[str, float]]
    stage_per_part_ms: dict[str, float]
    total_per_part_ms: float
    budget_ms: float = BUDGET_MS
    passed: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def throughput_fps(self) -> float:
        return self.n_frames / max(self.wall_s, 1e-9)


def run_bench(cfg: SorterConfig, spec: str, log_path: str | Path) -> BenchResult:
    pipeline, records, timing = replay_decisions(cfg, spec, log_path, bench=True)
    ends = [r for r in records if r["kind"] == "session_end"]
    end = ends[-1] if ends else {}
    n_parts = int(end.get("n_parts", 0))
    stages = pipeline.stage_timings()
    per_part: dict[str, float] = {}
    for name, s in stages.items():
        per_part[name] = s["mean"] * s["count"] / n_parts if n_parts else 0.0
    n_frames = len(timing["all_ms"])
    per_frame = summarize(timing["all_ms"])
    result = BenchResult(
        recording=str(spec),
        cpu=cpu_model(),
        fps=float(pipeline.cfg.camera.fps),
        n_frames=n_frames,
        n_processed=len(timing["processed_ms"]),
        n_parts=n_parts,
        wall_s=timing["wall_s"],
        per_frame=per_frame,
        per_processed=summarize(timing["processed_ms"]),
        stages=stages,
        stage_per_part_ms=per_part,
        total_per_part_ms=sum(per_part.values()),
    )
    result.passed = per_frame["mean"] <= BUDGET_MS
    result.notes.append(
        f"verdict is the mean cost per captured frame (skipped frames included) vs {BUDGET_MS} ms; "
        f"p99 is shown for information (the capture queue absorbs single slow frames)"
    )
    return result


def format_bench(r: BenchResult) -> str:
    lines = [
        f"bench: {r.recording}",
        f"CPU: {r.cpu}",
        f"NOTE: {NOT_FOR_ACCEPTANCE}.",
        f"{r.n_frames} frames ({r.n_processed} processed), {r.n_parts} parts, "
        f"wall {r.wall_s:.2f} s, throughput {r.throughput_fps:.0f} frames/s "
        f"(camera rate {r.fps:g} fps, {r.throughput_fps / r.fps:.2f}x real time)",
        "",
        f"{'stage':10s} {'n':>6s} {'mean':>8s} {'p50':>8s} {'p95':>8s} {'p99':>8s} {'max':>8s}"
        f" {'ms/part':>9s}   (ms per sample; ms/part = stage total / parts)",
    ]
    for name, s in r.stages.items():
        lines.append(
            f"{name:10s} {s['count']:6d} {s['mean']:8.2f} {s['p50']:8.2f} {s['p95']:8.2f}"
            f" {s['p99']:8.2f} {s['max']:8.2f} {r.stage_per_part_ms[name]:9.2f}"
        )
    lines.append(
        f"{'total/part':10s} {'':6s} {'':8s} {'':8s} {'':8s} {'':8s} {'':8s} "
        f"{r.total_per_part_ms:9.2f}"
    )
    lines.append("")
    for label, s in (("per input frame", r.per_frame), ("per processed frame", r.per_processed)):
        lines.append(
            f"{label:20s} mean {s['mean']:6.2f}  p50 {s['p50']:6.2f}  p95 {s['p95']:6.2f}  "
            f"p99 {s['p99']:6.2f}  max {s['max']:6.2f} ms"
        )
    lines.append("")
    verdict = "PASS" if r.passed else "FAIL"
    lines.append(
        f"{verdict}: mean {r.per_frame['mean']:.2f} ms per captured frame vs budget "
        f"{r.budget_ms} ms (p99 {r.per_frame['p99']:.2f} ms) [{r.cpu}; not an acceptance result]"
    )
    lines.extend(f"note: {n}" for n in r.notes)
    return "\n".join(lines)


# --------------------------------------------------------------------------- scenes


def scene_for_cfg(cfg: SorterConfig, **kw: Any) -> Any:
    from .synthetic import SceneConfig

    return SceneConfig(
        width=cfg.camera.width,
        height=cfg.camera.height,
        fps=cfg.camera.fps,
        speed_in_s=cfg.belt.speed_in_s,
        loop_length_in=cfg.belt.loop_length_in,
        **kw,
    )


def write_synthetic_recording(folder: str | Path, parts: list[Any], scene: Any = None) -> Path:
    """Render ``parts`` into a zstd recording at ``folder`` (must not exist). Deterministic."""
    from .frames import Frame
    from .recording import RecordingWriter
    from .synthetic import SceneConfig, SyntheticRenderer

    scene = scene or SceneConfig()
    renderer = SyntheticRenderer(scene, parts)
    n = int((max(p.t_enter_s for p in parts) + 1.5) * scene.fps)
    writer = RecordingWriter(
        folder, scene.width, scene.height, scene.fps, meta={"source": "synthetic"}
    )
    for i in range(n):
        t = START_NS + round(i * 1e9 / scene.fps)
        if not writer.write(Frame(i, t, t, renderer.render(i / scene.fps, i))):
            raise RuntimeError("recording writer dropped a frame")
    writer.close()
    return Path(folder)


def _golden_parts() -> dict[str, list[Any]]:
    from .synthetic import PartSpec

    return {
        # one of every in-scope class, singulated
        "mixed_classes": [
            PartSpec("bolt_025", 0.3, 1.0, 40.0),
            PartSpec("nut_025", 1.0, 1.3, 10.0),
            PartSpec("washer_025", 1.7, 0.9, 0.0),
            PartSpec("bolt_0375", 2.4, 1.1, 200.0),
            PartSpec("nut_0375", 3.1, 1.0, 20.0),
            PartSpec("washer_0375", 3.8, 1.2, 0.0),
        ],
        # two close parts (a rework group), then singulated parts
        "rework_group": [
            PartSpec("nut_0375", 0.3, 1.0, 10.0),
            PartSpec("washer_025", 0.5, 1.2, 0.0),
            PartSpec("bolt_025", 1.6, 1.0, 120.0),
            PartSpec("washer_0375", 2.6, 1.0, 0.0),
        ],
        # out-of-scope near misses go to MISC
        "out_of_scope": [
            PartSpec("coin", 0.3, 1.0, 0.0),
            PartSpec("nut_025", 1.0, 1.0, 30.0),
            PartSpec("washer_0312", 1.7, 1.1, 0.0),
            PartSpec("washer_025", 2.4, 0.9, 0.0),
        ],
    }


GOLDEN_SCENARIOS = tuple(_golden_parts())


def golden_parts(name: str) -> list[Any]:
    return _golden_parts()[name]


# --------------------------------------------------------------------------- stride guard

KEY_FEATURES: dict[str, dict[str, str]] = {
    # feature name -> nominal dimension key in synthetic.PART_DIMS
    "bolt_025": {"shank_width_in": "shank", "head_width_in": "head_w", "total_length_in": "length"},
    "bolt_0375": {
        "shank_width_in": "shank",
        "head_width_in": "head_w",
        "total_length_in": "length",
    },
    "nut_025": {"across_flats_in": "af", "hole_diameter_in": "bore"},
    "nut_0375": {"across_flats_in": "af", "hole_diameter_in": "bore"},
    "washer_025": {"minrect_long_in": "od", "hole_diameter_in": "id"},
    "washer_0375": {"minrect_long_in": "od", "hole_diameter_in": "id"},
}
OUT_OF_SCOPE_KINDS = ("coin", "washer_0312")


def stride_parts(n_parts: int = 60, seed: int = 11, spacing_s: float = 0.7) -> list[Any]:
    """Deterministic singulated stream: all six classes equally, plus ~1/6 near-miss
    out-of-scope parts (coin, 5/16 washer), shuffled."""
    from .synthetic import PART_KINDS, PartSpec

    rng = np.random.default_rng(seed)
    n_oos = max(2, n_parts // 6)
    kinds = [PART_KINDS[i % len(PART_KINDS)] for i in range(n_parts - n_oos)]
    kinds += [OUT_OF_SCOPE_KINDS[i % len(OUT_OF_SCOPE_KINDS)] for i in range(n_oos)]
    rng.shuffle(kinds)
    parts, t = [], 0.3
    for kind in kinds:
        ang = float(rng.uniform(0, 360)) if kind.startswith("bolt") else float(rng.uniform(0, 60))
        parts.append(PartSpec(str(kind), t, float(rng.uniform(0.55, 1.45)), ang))
        t += spacing_s
    return parts


@dataclass
class StrideRun:
    frame_stride: int
    max_feature_frames: int
    n_parts: int
    n_correct: int
    n_contaminated: int
    accuracy: float
    # class -> feature -> (median, MAD) of the part-median values
    feature_stats: dict[str, dict[str, tuple[float, float]]]
    # class -> feature -> median |value - nominal|
    feature_error: dict[str, dict[str, float]]
    median_error: float  # median over every (part, key feature) abs error
    mean_frames_used: float
    error: str = ""  # a run that cannot be scored at all (fails the guard)
    note: str = ""  # informational, e.g. a split track


@dataclass
class StrideComparison:
    runs: list[StrideRun]
    baseline: StrideRun
    default: StrideRun
    accuracy_loss_pp: float
    worst_error_growth_in: float
    worst_error_key: str
    median_error_growth_in: float
    passed: bool
    cpu: str


def _mad(values: list[float]) -> float:
    a = np.asarray(values, np.float64)
    return float(np.median(np.abs(a - np.median(a)))) if a.size else float("nan")


def score_run(
    records: list[dict[str, Any]],
    parts: list[Any],
    stride: int,
    mff: int,
    speed_in_s: float = 4.0,
    left_edge_in: float = -(1280 - 1) / 2 / 367.0,
) -> StrideRun:
    """Score one run against the known part list. Tracks are matched to parts by belt position
    (the part's centre is at ``left_edge_in - speed * t_enter`` in the tracker's belt frame), so a
    split or merged track is scored, not hidden. A part is correct only if every track that
    matched it went to the expected bin; any track in a class bin other than the part's own
    (or any unmatched track in a class bin) is contamination."""
    from .synthetic import PART_DIMS

    part_recs = [r for r in records if r["kind"] == "part"]
    out_of: dict[int, str] = {}
    for g in (r for r in records if r["kind"] == "group"):
        for tid in g["member_track_ids"]:
            out_of[tid] = g["output"]
    centres = [left_edge_in - speed_in_s * p.t_enter_s for p in parts]
    matched: list[list[dict[str, Any]]] = [[] for _ in parts]
    unmatched: list[dict[str, Any]] = []
    for rec in part_recs:
        mid = (rec["belt_x_lead_in"] + rec["belt_x_trail_in"]) / 2.0
        i = int(np.argmin([abs(mid - c) for c in centres]))
        if abs(mid - centres[i]) < 1.4:
            matched[i].append(rec)
        else:
            unmatched.append(rec)
    bins = (Output.MISC.value, Output.REWORK.value, None)
    correct = contaminated = 0
    values: dict[str, dict[str, list[float]]] = {}
    errors: dict[str, dict[str, list[float]]] = {}
    for spec, recs in zip(parts, matched, strict=True):
        want = Output.MISC.value if spec.kind in OUT_OF_SCOPE_KINDS else spec.kind
        outs = [out_of.get(r["track_id"]) for r in recs]
        correct += bool(outs) and all(o == want for o in outs)
        contaminated += any(o not in bins and o != spec.kind for o in outs)
        if not recs:
            continue
        best = max(recs, key=lambda r: r["n_frames"])
        for feat, dim in KEY_FEATURES.get(spec.kind, {}).items():
            v = best["median"].get(feat)
            if v is None:
                continue
            values.setdefault(spec.kind, {}).setdefault(feat, []).append(float(v))
            nominal = PART_DIMS[spec.kind][dim]
            errors.setdefault(spec.kind, {}).setdefault(feat, []).append(abs(float(v) - nominal))
    contaminated += sum(out_of.get(r["track_id"]) not in bins for r in unmatched)
    stats = {
        c: {f: (float(np.median(v)), _mad(v)) for f, v in fs.items()} for c, fs in values.items()
    }
    err = {c: {f: float(np.median(v)) for f, v in fs.items()} for c, fs in errors.items()}
    flat = [e for fs in errors.values() for v in fs.values() for e in v]
    notes = []
    if len(part_recs) != len(parts):
        notes.append(f"{len(part_recs)} tracks for {len(parts)} parts")
    return StrideRun(
        stride,
        mff,
        len(parts),
        correct,
        contaminated,
        correct / len(parts),
        stats,
        err,
        float(np.median(flat)) if flat else float("nan"),
        float(np.mean([r["n_frames"] for r in part_recs])) if part_recs else 0.0,
        error="",
        note="; ".join(notes),
    )


def compare_strides(
    cfg: SorterConfig,
    n_parts: int = 60,
    seed: int = 11,
    configs: tuple[tuple[int, int], ...] = ((1, 20), (1, 10), (2, 20), (2, 10)),
    progress: Any = None,
) -> StrideComparison:
    """Run one deterministic scene through pipelines at several (frame_stride, max_feature_frames)
    settings (the scene is rendered once and shared) and apply the guard: the defaults (stride 2 /
    10 frames, the last config) lose <= MAX_ACCURACY_LOSS_PP of accuracy against the first config
    (stride 1 / 20 frames) and no key feature's median abs error grows by more than
    MAX_FEATURE_ERROR_GROWTH_IN."""
    from .clock import ManualClock
    from .eventlog import EventLog
    from .frames import Frame
    from .output import MockSerialAdapter
    from .pipeline import Pipeline, synthetic_calibration
    from .synthetic import SyntheticRenderer

    parts = stride_parts(n_parts, seed)
    scene = scene_for_cfg(cfg)
    cal, flat = synthetic_calibration(scene)
    renderer = SyntheticRenderer(scene, parts)
    rigs = []
    for stride, mff in configs:
        data = cfg.model_dump()
        data["pipeline"].update(frame_stride=stride, max_feature_frames=mff)
        c = SorterConfig.model_validate(data)
        log = EventLog(keep_in_memory=100_000)
        pl = Pipeline(
            c,
            cal,
            ManualClock(0),
            MockSerialAdapter(),
            log,
            flat=flat,
            image_size=(scene.width, scene.height),
        )
        rigs.append((stride, mff, pl, log))
    n = int((max(p.t_enter_s for p in parts) + 1.5) * scene.fps)
    for i in range(n):
        img = renderer.render(i / scene.fps, i)
        t = START_NS + round(i * 1e9 / scene.fps)
        frame = Frame(i, t, t, img)
        for _, _, pl, _ in rigs:
            pl.process(frame)
        if progress and i % 500 == 0:
            progress(i, n)
    runs = []
    for stride, mff, pl, log in rigs:
        pl.close()
        runs.append(
            score_run(
                log.recent(),
                parts,
                stride,
                mff,
                cfg.belt.speed_in_s,
                -(scene.width - 1) / 2 / scene.px_per_in,
            )
        )
    baseline, default = runs[0], runs[-1]
    loss_pp = (baseline.accuracy - default.accuracy) * 100.0
    worst, worst_key = float("-inf"), ""
    for cls, feats in default.feature_error.items():
        for feat, e in feats.items():
            growth = e - baseline.feature_error.get(cls, {}).get(feat, e)
            if growth > worst:
                worst, worst_key = growth, f"{cls}.{feat}"
    if worst == float("-inf"):
        worst = float("nan")
    med_growth = default.median_error - baseline.median_error
    passed = (
        not baseline.error
        and not default.error
        and loss_pp <= MAX_ACCURACY_LOSS_PP
        and worst <= MAX_FEATURE_ERROR_GROWTH_IN
    )
    return StrideComparison(
        runs, baseline, default, loss_pp, worst, worst_key, med_growth, passed, cpu_model()
    )


def format_strides(c: StrideComparison) -> str:
    lines = [
        f"stride accuracy guard ({c.baseline.n_parts} parts, deterministic synthetic scene)",
        f"CPU: {c.cpu}",
        "",
        f"{'frame_stride':>12s} {'max_feat':>9s} {'accuracy':>9s} {'contam':>7s} "
        f"{'med err (in)':>13s} {'frames/part':>12s}",
    ]
    for r in c.runs:
        tag = ""
        if r is c.baseline:
            tag = "  <- baseline"
        elif r is c.default:
            tag = "  <- defaults"
        msg = f"  [{r.error or r.note}]" if (r.error or r.note) else ""
        lines.append(
            f"{r.frame_stride:12d} {r.max_feature_frames:9d} {r.accuracy:9.1%} "
            f"{r.n_contaminated:7d} {r.median_error:13.4f} {r.mean_frames_used:12.1f}{tag}{msg}"
        )
    lines += ["", "per-class key features: median (MAD) in inches, defaults vs baseline"]
    for cls in sorted(c.default.feature_stats):
        for feat, (med, mad) in c.default.feature_stats[cls].items():
            bmed, bmad = c.baseline.feature_stats.get(cls, {}).get(feat, (float("nan"),) * 2)
            e, be = c.default.feature_error[cls][feat], c.baseline.feature_error[cls][feat]
            lines.append(
                f"  {cls:12s} {feat:17s} {med:.4f} ({mad:.4f})  vs  {bmed:.4f} ({bmad:.4f})"
                f"   median err {e:.4f} vs {be:.4f}"
            )
    lines += [
        "",
        f"accuracy loss, defaults vs stride 1 / 20 frames: {c.accuracy_loss_pp:+.2f} pp "
        f"(limit {MAX_ACCURACY_LOSS_PP} pp)",
        f"worst key-feature median-error growth: {c.worst_error_growth_in:+.4f} in at "
        f"{c.worst_error_key} (limit {MAX_FEATURE_ERROR_GROWTH_IN} in); "
        f"overall median error growth {c.median_error_growth_in:+.4f} in",
        f"{'PASS' if c.passed else 'FAIL'}: stride 2 / 10 frames "
        f"{'meets' if c.passed else 'does NOT meet'} the accuracy guard",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI glue


def cmd_bench(args: Any, cfg: SorterConfig) -> int:
    import tempfile

    if args.compare_strides:
        comp = compare_strides(
            cfg,
            args.parts,
            progress=lambda i, n: print(f"  frame {i}/{n}", file=sys.stderr, flush=True),
        )
        print(format_strides(comp))
        return 0 if comp.passed else 1
    if not args.recording:
        print("error: give a recording (or use --compare-strides)", file=sys.stderr)
        return 2
    from .cli import resolve_recording

    spec = (
        args.recording
        if args.recording == "synthetic"
        else str(resolve_recording(cfg, args.recording))
    )
    with tempfile.TemporaryDirectory(prefix="sorter-bench-") as tmp:
        result = run_bench(cfg, spec, Path(tmp) / "decision_log.jsonl")
    print(format_bench(result))
    return 0 if result.passed else 1


def cmd_replay_compare(args: Any, cfg: SorterConfig, spec: str) -> int:
    """``replay --compare``: re-run ``spec`` and diff its decisions against the given log."""
    import tempfile

    expected_path = Path(args.compare)
    if not expected_path.is_file():
        print(f"error: no decision log at {expected_path}", file=sys.stderr)
        return 2
    expected = read_log(expected_path)
    with tempfile.TemporaryDirectory(prefix="sorter-compare-") as tmp:
        out = Path(args.decision_log) if args.decision_log else Path(tmp) / "decision_log.jsonl"
        if out.resolve() == expected_path.resolve():
            print("error: --decision-log must differ from the --compare log", file=sys.stderr)
            return 2
        _, actual, _ = replay_decisions(cfg, spec, out)
    strict = bool(getattr(args, "strict", False))
    ftol = getattr(args, "float_tol", FLOAT_ABS_TOL)
    tol = STRICT if strict else CompareTol(abs_tol=ftol, rel_tol=ftol)
    dev = [0.0]
    problems = diff_decisions(expected, actual, tol=tol, dev=dev)
    n = {k: len(v) for k, v in decision_view(actual).items()}
    if problems:
        print(f"MISMATCH: decisions differ from {expected_path} ({len(problems)} shown)")
        for line in problems:
            print(f"  {line}")
        return 1
    mode = "strict" if strict else f"float tol {ftol:g}"
    print(
        f"MATCH: decisions match {expected_path} (timings ignored; {mode}; "
        f"max float deviation {dev[0]:.3g}; records: {n})"
    )
    return 0
