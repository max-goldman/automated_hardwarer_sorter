"""Render a ``ReportData`` as CSV and as a self-contained HTML page (inline CSS, no assets)."""

from __future__ import annotations

import csv
import io
from html import escape
from pathlib import Path

from .metrics import FAIL, NA, OUTPUT_COLS, PASS, TRUE_ROWS, Check, ReportData

PCT_CHECKS = {"Accuracy", "Bin contamination", "Rework rate"}


def _fmt(v: float | int | None, pct: bool = False, nd: int = 2) -> str:
    if v is None:
        return "n/a"
    if pct:
        return f"{v * 100:.{nd}f}%"
    if isinstance(v, int):
        return str(v)
    return f"{v:.{nd}f}"


def _measured(c: Check) -> str:
    text = _fmt(c.measured, pct=c.name in PCT_CHECKS)
    return f"{text} {c.unit}".strip() if c.measured is not None else text


# --------------------------------------------------------------------------- CSV


def to_csv(r: ReportData) -> str:
    """Long-form CSV: ``section,item,field,value`` (one fact per row)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["section", "item", "field", "value"])

    def row(section: str, item: str, field: str, value: object) -> None:
        w.writerow([section, item, field, "" if value is None else value])

    for c in r.checks:
        row("targets", c.name, "measured", c.measured)
        row("targets", c.name, "target", c.target_text)
        row("targets", c.name, "verdict", c.verdict)
    row("targets", "Overall", "verdict", r.overall)
    row("summary", "sessions", "names", ";".join(r.sessions))
    for k, v in (
        ("parts_fed", r.n_parts_fed),
        ("parts_detected", r.n_detected),
        ("groups", r.n_groups),
        ("accuracy", r.accuracy),
        ("contamination", r.contamination),
        ("contaminated_parts", r.n_contaminated),
        ("misc_rate", r.misc_rate),
        ("rework_rate", r.rework_rate),
    ):
        row("summary", k, "value", v)
    for k, v in r.counts.items():
        row("summary", f"groups_{k}", "value", v)
    for true in TRUE_ROWS:
        for out in OUTPUT_COLS:
            row("confusion", true, out, r.confusion[true][out])
    for cls, m in r.per_class.items():
        for f, v in m.items():
            row("per_class", cls, f, v)
    for k, b in r.seam.items():
        for f, v in b.items():
            row("seam", k, f, v)
    for stage, s in r.stages.items():
        for f, v in s.items():
            row("stage_latency_ms", stage, f, v)
    if r.decision_latency:
        for f, v in r.decision_latency.items():
            row("decision_latency_ms", "capture_to_decision", f, v)
    row("throughput", "frames", "n_frames", r.n_frames)
    row("throughput", "frames", "n_skipped", r.n_skipped)
    row("throughput", "frames", "fps", r.fps)
    row("throughput", "frames", "n_dropped", r.n_dropped)
    row("throughput", "frames", "mean_ms_per_frame", r.mean_frame_ms)
    row("sends", "sends", "n_sends", r.n_sends)
    row("sends", "sends", "n_deadline_misses", r.n_deadline_misses)
    for f, v in (r.send_error_ms or {}).items():
        row("send_error_ms", "abs_error", f, v)
    for i, msg in enumerate(r.warnings, 1):
        row("warnings", str(i), "text", msg)
    return buf.getvalue()


# --------------------------------------------------------------------------- HTML

CSS = """
body{font:14px/1.45 system-ui,Segoe UI,Helvetica,Arial,sans-serif;margin:2rem auto;max-width:1100px;
padding:0 1rem;color:#1d2530;background:#fff}
h1{font-size:1.6rem;margin:.2rem 0}h2{font-size:1.15rem;margin:2rem 0 .5rem;border-bottom:1px solid #d5dbe3}
table{border-collapse:collapse;margin:.4rem 0}th,td{border:1px solid #d5dbe3;padding:.25rem .6rem;
text-align:right}th:first-child,td:first-child{text-align:left}th{background:#f1f4f8}
.pass{background:#d9f2e0;color:#14532d;font-weight:600;text-align:center}
.fail{background:#fbd9d9;color:#7f1d1d;font-weight:600;text-align:center}
.na,.info{background:#eceff3;color:#475467;text-align:center}
.badge{display:inline-block;padding:.2rem .8rem;border-radius:4px;font-weight:700}
.warn{color:#8a4b00}.muted{color:#667085}.diag{font-weight:600}
"""


def _cls(verdict: str) -> str:
    return {PASS: "pass", FAIL: "fail", NA: "na"}.get(verdict, "info")


def _table(head: list[str], rows: list[list[str]], classes: list[list[str]] | None = None) -> str:
    out = ["<table><thead><tr>" + "".join(f"<th>{escape(h)}</th>" for h in head) + "</tr></thead>"]
    out.append("<tbody>")
    for i, row in enumerate(rows):
        cells = []
        for j, c in enumerate(row):
            klass = classes[i][j] if classes else ""
            cells.append(
                f'<td class="{klass}">{escape(c)}</td>' if klass else f"<td>{escape(c)}</td>"
            )
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def to_html(r: ReportData) -> str:
    p: list[str] = []
    p.append('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">')
    p.append("<title>Hardware sorter: accuracy and latency report</title>")
    p.append(f"<style>{CSS}</style></head><body>")
    p.append("<h1>Hardware sorter: accuracy and latency report</h1>")
    p.append(
        f'<p>Overall: <span class="badge {_cls(r.overall)}">{escape(r.overall)}</span> '
        f'<span class="muted">sessions: {escape(", ".join(r.sessions))}</span></p>'
    )
    for msg in r.warnings:
        p.append(f'<p class="warn">Warning: {escape(msg)}</p>')

    p.append("<h2>Performance targets</h2>")
    rows = [[c.name, _measured(c), c.target_text, c.verdict] for c in r.checks]
    cls = [["", "", "", _cls(c.verdict)] for c in r.checks]
    p.append(_table(["Target", "Measured", "Required", "Result"], rows, cls))
    p.append(
        '<p class="muted">Send-time error and rework rate are advisory and not part of the overall result.</p>'
    )

    p.append("<h2>Summary</h2>")
    k = r.counts
    srows = [
        ["Parts fed", str(r.n_parts_fed)],
        ["Parts detected", str(r.n_detected)],
        ["Groups", str(r.n_groups)],
        ["Groups correct / wrong / phantom", f"{k['correct']} / {k['wrong']} / {k['phantom']}"],
        ["Undetected parts", str(k["undetected"])],
        ["Accuracy", _fmt(r.accuracy, True)],
        ["Bin contamination", f"{_fmt(r.contamination, True)} ({r.n_contaminated} parts)"],
        ["MISC rate", _fmt(r.misc_rate, True)],
        ["REWORK rate", _fmt(r.rework_rate, True)],
    ]
    p.append(_table(["Metric", "Value"], srows))

    p.append("<h2>Confusion matrix</h2>")
    p.append('<p class="muted">Rows: true identity. Columns: output the group was sent to.</p>')
    head = ["true \\ output", *OUTPUT_COLS, "total"]
    crow, ccls = [], []
    mx = max((v for row in r.confusion.values() for v in row.values()), default=0) or 1
    for true in TRUE_ROWS:
        vals = [r.confusion[true][o] for o in OUTPUT_COLS]
        crow.append([true, *map(str, vals), str(sum(vals))])
        ccls.append([""] * (len(head)))
    body = [
        "<table><thead><tr>"
        + "".join(f"<th>{escape(h)}</th>" for h in head)
        + "</tr></thead><tbody>"
    ]
    for ri, true in enumerate(TRUE_ROWS):
        cells = [f"<td>{escape(true)}</td>"]
        for o in OUTPUT_COLS:
            v = r.confusion[true][o]
            diag = true == o
            hue = "34,139,84" if diag else "200,50,50"
            alpha = 0.08 + 0.6 * v / mx if v else 0
            style = f' style="background:rgba({hue},{alpha:.2f})"' if v else ""
            cells.append(f'<td class="{"diag" if diag else ""}"{style}>{v}</td>')
        cells.append(f"<td>{crow[ri][-1]}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    body.append("</tbody></table>")
    p.append("".join(body))

    p.append("<h2>Per-class precision and recall</h2>")
    prow = [
        [c, _fmt(m["precision"], True, 1), _fmt(m["recall"], True, 1), str(int(m["support"] or 0))]
        for c, m in r.per_class.items()
    ]
    p.append(_table(["Class", "Precision", "Recall", "Support"], prow))

    p.append("<h2>On-seam parts</h2>")
    srows = [
        [
            "on seam" if key == "on_seam" else "off seam",
            str(b["groups"]),
            str(b["correct"]),
            _fmt(b["accuracy"], True),
        ]
        for key, b in r.seam.items()
    ]
    p.append(_table(["Parts", "Groups graded", "Correct", "Accuracy"], srows))

    p.append("<h2>Latency (ms)</h2>")
    lrows = []
    if r.decision_latency:
        d = r.decision_latency
        lrows.append(
            ["capture to decision (per part)", *(_fmt(d[f]) for f in ("p50", "p95", "p99", "max"))]
        )
    for stage, s in r.stages.items():
        lrows.append([f"stage: {stage}", *(_fmt(s[f], nd=3) for f in ("p50", "p95", "p99", "max"))])
    p.append(_table(["Stage", "p50", "p95", "p99", "max"], lrows))
    p.append(
        f'<p class="muted">Mean per processed frame (sum of stage means): {_fmt(r.mean_frame_ms, nd=3)} ms. '
        "With several sessions p95/p99/max are the worst session.</p>"
    )

    p.append("<h2>Throughput and sends</h2>")
    trows = [
        ["Frames", _fmt(r.n_frames)],
        ["Frames skipped by design", _fmt(r.n_skipped)],
        ["Capture fps", _fmt(r.fps, nd=1)],
        ["Frame drops", _fmt(r.n_dropped)],
        ["Sends", str(r.n_sends)],
        ["Deadline misses", str(r.n_deadline_misses)],
    ]
    if r.send_error_ms:
        e = r.send_error_ms
        trows += [
            ["Send-time error mean |ms|", _fmt(e["mean"], nd=3)],
            ["Send-time error p99 |ms|", _fmt(e["p99"], nd=3)],
            ["Send-time error max |ms|", _fmt(e["max"], nd=3)],
        ]
    p.append(_table(["Item", "Value"], trows))
    p.append("</body></html>")
    return "".join(p)


def write_report(r: ReportData, out_dir: str | Path, stem: str = "report") -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path, html_path = out / f"{stem}.csv", out / f"{stem}.html"
    csv_path.write_text(to_csv(r), encoding="utf-8")
    html_path.write_text(to_html(r), encoding="utf-8")
    return csv_path, html_path
