"""Report metrics: accuracy, contamination, rates, confusion matrix, latency and send timing.

Reads only the decision-log JSONL, the label store and the session folder (via
``hwsorter.grading``); never vision internals. Correctness comes from ``grade_session``;
this module adds the output-side statistics and the pass/fail verdicts.

Definitions (all over the sessions given, aggregated):

* accuracy = correct / (correct + wrong + phantom + undetected), from ``GradeReport`` (group
  level: a group of several parts counts once, as the correct-output table assigns one bin).
* contamination = (in-scope parts in another class's bin + out-of-scope parts in any class
  bin) / parts fed (``parts_fed`` from the label store, else the detected parts).
* MISC / REWORK rate = share of detected parts whose group went to MISC / REWORK.
* confusion matrix: rows = true identity (6 classes + ``out_of_scope``), columns = the 8
  outputs; a part counts at its group's decided output. Excluded (phantom) parts are skipped.
* precision(c) = M[c][c] / column sum of c; recall(c) = M[c][c] / row sum of c.
* decision latency = ``timings.latency_ms`` of each part record (capture of the last frame
  used -> decision), exact percentiles over all parts. Per-stage rows come from
  ``session_end`` ``stages_ms``; with several sessions p50 and mean are count-weighted and
  p95/p99/max are the worst session (percentiles cannot be merged exactly).
* send-time error = ``t_actual_ns - t_send_ns`` of each send record.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..contracts import Output, PartClass
from ..grading import GradeReport, LabelStore, SessionData, grade_session, load_session
from ..grading.grade import CORRECT, PHANTOM, WRONG

OOS = "out_of_scope"
TRUE_ROWS: tuple[str, ...] = (*(c.value for c in PartClass), OOS)
OUTPUT_COLS: tuple[str, ...] = tuple(o.value for o in Output)
STAGES = ("preprocess", "segment", "seam", "track", "features", "fuse", "classify", "decide")
NS_PER_MS = 1e6
EPS = 1e-12

PASS, FAIL, NA, INFO = "PASS", "FAIL", "N/A", "INFO"


@dataclass(frozen=True)
class Targets:
    """Performance-requirement thresholds (brief: Performance requirements)."""

    accuracy_min: float = 0.95
    contamination_max: float = 0.01
    latency_p99_ms_max: float = 250.0
    mean_frame_ms_max: float = 8.3
    frame_drops_max: int = 0
    deadline_misses_max: int = 0
    send_error_ms_max: float = 10.0  # suggested; advisory, not part of the overall verdict


@dataclass
class Check:
    name: str
    measured: float | int | None
    target_text: str
    verdict: str  # PASS | FAIL | N/A | INFO
    unit: str = ""
    required: bool = True  # counts toward the overall verdict


@dataclass
class SessionStats:
    """What one session folder contributes."""

    name: str
    grade: GradeReport
    parts: list[dict[str, Any]]  # one per detected part: track_id, group output, on_seam
    part_latency_ms: list[float]
    sends: list[dict[str, Any]]
    start: dict[str, Any]
    end: dict[str, Any] | None
    n_groups: int
    identities: list[tuple[str, list[PartClass | None]]]  # (group output, true identity per part)


@dataclass
class ReportData:
    sessions: list[str]
    targets: Targets
    n_parts_fed: int
    n_detected: int
    n_groups: int
    counts: dict[str, int]  # correct / wrong / phantom / incomplete / undetected
    accuracy: float | None
    contamination: float | None
    n_contaminated: int
    misc_rate: float | None
    rework_rate: float | None
    confusion: dict[str, dict[str, int]]
    per_class: dict[str, dict[str, float | None]]
    seam: dict[str, dict[str, Any]]  # on_seam / off_seam -> {groups, correct, accuracy}
    stages: dict[str, dict[str, float]]
    decision_latency: dict[str, float] | None
    mean_frame_ms: float | None
    n_frames: int | None
    n_skipped: int | None
    fps: float | None
    n_dropped: int | None
    n_deadline_misses: int
    n_sends: int
    send_error_ms: dict[str, float] | None
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def overall(self) -> str:
        req = [c for c in self.checks if c.required]
        if any(c.verdict == FAIL for c in req):
            return FAIL
        if any(c.verdict == NA for c in req):
            return NA
        return PASS


# --------------------------------------------------------------------------- reading


def _read_log_extras(log_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(part records, send records) of the last session in the log."""
    parts: list[dict[str, Any]] = []
    sends: list[dict[str, Any]] = []
    with log_path.open(encoding="utf-8") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            try:
                r = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = r.get("kind")
            if kind == "session_start":
                parts, sends = [], []
            elif kind == "part":
                parts.append(r)
            elif kind == "send":
                sends.append(r)
    return parts, sends


def _is_on_seam(rec: dict[str, Any]) -> bool:
    flags = rec.get("flags") or ()
    return bool(rec.get("on_seam")) or "ON_SEAM" in flags


def collect_session(
    session: SessionData, store: LabelStore, name: str | None = None
) -> SessionStats:
    match = store.rematch(session.parts)
    grade = grade_session(session, store, match)
    label_of = {i: store.get(k) for k, i in match.matched.items()}
    by_tid = {p.track_id: label_of.get(i) for i, p in enumerate(session.parts)}
    identities: list[tuple[str, list[PartClass | None]]] = []
    for gg in grade.groups:
        idents: list[PartClass | None] = []
        for tid in gg.member_track_ids:
            lab = by_tid.get(tid)
            if lab is not None:
                idents.extend(lab.resolved())
        identities.append((gg.predicted.value, idents))
    part_recs, sends = _read_log_extras(session.log_path)
    by_track = {int(r["track_id"]): r for r in part_recs}
    parts: list[dict[str, Any]] = []
    for g in grade.groups:
        for tid in g.member_track_ids:
            rec = by_track.get(tid, {})
            parts.append({"track_id": tid, "group_id": g.group_id, "on_seam": _is_on_seam(rec)})
    lat = []
    for r in part_recs:
        v = (r.get("timings") or {}).get("latency_ms")
        if isinstance(v, (int, float)):
            lat.append(float(v))
    return SessionStats(
        name=name or session.root.name,
        grade=grade,
        parts=parts,
        part_latency_ms=lat,
        sends=sends,
        start=session.start,
        end=session.end,
        n_groups=len(session.groups),
        identities=identities,
    )


def load_stats(root: str | Path, labels: str | Path | None = None) -> SessionStats:
    session = load_session(root)
    labels_path = Path(labels) if labels else session.root / "labels.jsonl"
    return collect_session(session, LabelStore(labels_path), name=session.root.name)


# --------------------------------------------------------------------------- aggregation


def _pct(values: list[float]) -> dict[str, float]:
    a = np.asarray(values, np.float64)
    return {
        "count": float(a.size),
        "mean": float(a.mean()),
        "p50": float(np.percentile(a, 50)),
        "p95": float(np.percentile(a, 95)),
        "p99": float(np.percentile(a, 99)),
        "max": float(a.max()),
    }


def _merge_stages(ends: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    per_stage: dict[str, list[dict[str, float]]] = {}
    for end in ends:
        stages = ((end.get("timings") or {}).get("stages_ms")) or {}
        for stage, s in stages.items():
            if isinstance(s, dict) and s.get("count"):
                per_stage.setdefault(stage, []).append(s)
    out: dict[str, dict[str, float]] = {}
    ordered = [s for s in STAGES if s in per_stage] + [s for s in per_stage if s not in STAGES]
    for stage in ordered:
        rows = per_stage[stage]
        n = sum(r["count"] for r in rows)
        out[stage] = {
            "count": float(n),
            "mean": sum(r["mean"] * r["count"] for r in rows) / n,
            "p50": sum(r["p50"] * r["count"] for r in rows) / n,
            "p95": max(r["p95"] for r in rows),
            "p99": max(r["p99"] for r in rows),
            "max": max(r["max"] for r in rows),
        }
    return out


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def build_report(stats: list[SessionStats], targets: Targets | None = None) -> ReportData:
    targets = targets or Targets()
    warnings: list[str] = []
    counts = dict.fromkeys(("correct", "wrong", "phantom", "incomplete", "undetected"), 0)
    n_fed = n_detected = n_groups = 0
    confusion = {r: dict.fromkeys(OUTPUT_COLS, 0) for r in TRUE_ROWS}
    n_contaminated = 0
    out_counts = dict.fromkeys(OUTPUT_COLS, 0)
    seam = {k: {"groups": 0, "correct": 0} for k in ("on_seam", "off_seam")}
    n_parts_out = 0

    for st in stats:
        g = st.grade
        counts["correct"] += g.count(CORRECT)
        counts["wrong"] += g.count(WRONG)
        counts["phantom"] += g.count(PHANTOM)
        counts["incomplete"] += g.count("incomplete")
        counts["undetected"] += g.n_undetected or 0
        n_detected += g.n_detected_parts
        n_groups += len(g.groups)
        if g.parts_fed is None:
            warnings.append(f"{st.name}: parts fed not set; accuracy ignores undetected parts")
            n_fed += g.n_detected_parts
        else:
            n_fed += g.parts_fed
        if g.n_unlabeled_parts:
            warnings.append(f"{st.name}: {g.n_unlabeled_parts} detected part(s) unlabelled")
        if g.unmatched_labels:
            warnings.append(f"{st.name}: {len(g.unmatched_labels)} label(s) match no part")
        if g.n_overcount:
            warnings.append(f"{st.name}: labels exceed parts fed by {g.n_overcount}")
        if st.end is None:
            warnings.append(f"{st.name}: no session_end record (timings unavailable)")

        seam_groups = {p["group_id"] for p in st.parts if p["on_seam"]}
        for gg in g.groups:
            out = gg.predicted.value
            out_counts[out] += len(gg.member_track_ids)
            n_parts_out += len(gg.member_track_ids)
            if gg.status in (CORRECT, WRONG, PHANTOM):
                bucket = seam["on_seam" if gg.group_id in seam_groups else "off_seam"]
                bucket["groups"] += 1
                bucket["correct"] += gg.status == CORRECT
        # confusion matrix + contamination: needs per-part labels via the grading rematch
        n_contaminated += _fill_confusion(st, confusion)

    accuracy_den = counts["correct"] + counts["wrong"] + counts["phantom"] + counts["undetected"]
    accuracy = _ratio(counts["correct"], accuracy_den)
    contamination = _ratio(n_contaminated, n_fed)

    per_class: dict[str, dict[str, float | None]] = {}
    for c in (p.value for p in PartClass):
        tp = confusion[c][c]
        col = sum(confusion[r][c] for r in TRUE_ROWS)
        row = sum(confusion[c].values())
        per_class[c] = {"precision": _ratio(tp, col), "recall": _ratio(tp, row), "support": row}

    for b in seam.values():
        b["accuracy"] = _ratio(b["correct"], b["groups"])

    stages = _merge_stages([st.end for st in stats if st.end])
    latencies = [v for st in stats for v in st.part_latency_ms]
    decision_latency = _pct(latencies) if latencies else None
    mean_frame = sum(s["mean"] for s in stages.values()) if stages else None

    ends = [st.end for st in stats if st.end]
    n_frames = sum(int(e.get("n_frames", 0)) for e in ends) if ends else None
    n_skipped = (
        sum(int(((e.get("timings") or {}).get("n_skipped")) or 0) for e in ends) if ends else None
    )
    n_dropped = sum(int(e.get("n_dropped", 0)) for e in ends) if ends else None
    fps = None
    if ends:
        dur = sum(float(e.get("duration_ns", 0)) for e in ends)
        if dur > 0 and n_frames:
            fps = n_frames * 1e9 / dur

    sends = [s for st in stats for s in st.sends]
    n_miss = sum(1 for s in sends if s.get("miss", "none") != "none")
    errs = [
        abs(s["t_actual_ns"] - s["t_send_ns"]) / NS_PER_MS
        for s in sends
        if isinstance(s.get("t_actual_ns"), (int, float))
        and isinstance(s.get("t_send_ns"), (int, float))
    ]
    send_err = _pct(errs) if errs else None

    rep = ReportData(
        sessions=[st.name for st in stats],
        targets=targets,
        n_parts_fed=n_fed,
        n_detected=n_detected,
        n_groups=n_groups,
        counts=counts,
        accuracy=accuracy,
        contamination=contamination,
        n_contaminated=n_contaminated,
        misc_rate=_ratio(out_counts["misc"], n_parts_out),
        rework_rate=_ratio(out_counts["rework"], n_parts_out),
        confusion=confusion,
        per_class=per_class,
        seam=seam,
        stages=stages,
        decision_latency=decision_latency,
        mean_frame_ms=mean_frame,
        n_frames=n_frames,
        n_skipped=n_skipped,
        fps=fps,
        n_dropped=n_dropped,
        n_deadline_misses=n_miss,
        n_sends=len(sends),
        send_error_ms=send_err,
        warnings=warnings,
    )
    rep.checks = evaluate(rep)
    return rep


def _fill_confusion(st: SessionStats, confusion: dict[str, dict[str, int]]) -> int:
    """Add this session's labelled parts to ``confusion``; return the contaminated-part count."""
    contaminated = 0
    for out, idents in st.identities:
        for ident in idents:
            row = OOS if ident is None else ident.value
            confusion[row][out] += 1
            if out in (Output.MISC.value, Output.REWORK.value):
                continue
            if row == OOS or row != out:
                contaminated += 1
    return contaminated


# --------------------------------------------------------------------------- verdicts


def _le(x: float | None, limit: float) -> str:
    return NA if x is None else (PASS if x <= limit + EPS else FAIL)


def _ge(x: float | None, limit: float) -> str:
    return NA if x is None else (PASS if x >= limit - EPS else FAIL)


def evaluate(r: ReportData) -> list[Check]:
    t = r.targets
    p99 = r.decision_latency["p99"] if r.decision_latency else None
    miss = r.n_deadline_misses if r.n_sends or r.n_frames is not None else None
    err_max = r.send_error_ms["max"] if r.send_error_ms else None
    return [
        Check("Accuracy", r.accuracy, f">= {t.accuracy_min:.0%}", _ge(r.accuracy, t.accuracy_min)),
        Check(
            "Bin contamination",
            r.contamination,
            f"<= {t.contamination_max:.0%}",
            _le(r.contamination, t.contamination_max),
        ),
        Check(
            "Decision latency p99",
            p99,
            f"<= {t.latency_p99_ms_max:g} ms",
            _le(p99, t.latency_p99_ms_max),
            "ms",
        ),
        Check(
            "Mean processing time per frame",
            r.mean_frame_ms,
            f"<= {t.mean_frame_ms_max:g} ms",
            _le(r.mean_frame_ms, t.mean_frame_ms_max),
            "ms",
        ),
        Check(
            "Unexplained frame drops",
            r.n_dropped,
            f"<= {t.frame_drops_max}",
            _le(None if r.n_dropped is None else float(r.n_dropped), t.frame_drops_max),
        ),
        Check(
            "Send deadline misses",
            miss,
            f"<= {t.deadline_misses_max}",
            _le(None if miss is None else float(miss), t.deadline_misses_max),
        ),
        Check(
            "Send-time error (max, suggested)",
            err_max,
            f"<= {t.send_error_ms_max:g} ms",
            _le(err_max, t.send_error_ms_max),
            "ms",
            required=False,
        ),
        Check("Rework rate", r.rework_rate, "reported, no target", INFO, required=False),
    ]


def is_finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)
