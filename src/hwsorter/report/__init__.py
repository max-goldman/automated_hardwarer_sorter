"""Accuracy and latency report (task 013): graded sessions in, CSV + self-contained HTML out."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .metrics import ReportData, SessionStats, Targets, build_report, collect_session, load_stats
from .render import to_csv, to_html, write_report
from .rerun import RerunError, rerun_session

__all__ = [
    "ReportData",
    "RerunError",
    "SessionStats",
    "Targets",
    "build_report",
    "collect_session",
    "generate",
    "load_stats",
    "rerun_session",
    "to_csv",
    "to_html",
    "write_report",
]


def generate(
    sessions: list[str | Path],
    out_dir: str | Path | None = None,
    rerun: bool = False,
    cfg: Any = None,
) -> tuple[ReportData, Path, Path]:
    """Build and write the report for ``sessions`` (aggregated). Returns (data, csv, html).

    Default output folder: the first session folder (for a bare log file, its session root)."""
    from ..grading.session_log import find_log

    if not sessions:
        raise ValueError("at least one session is required")
    first_root, _ = find_log(sessions[0])
    out = Path(out_dir) if out_dir else first_root
    stats: list[SessionStats] = []
    for s in sessions:
        root, _ = find_log(s)
        labels = root / "labels.jsonl"
        if rerun:
            if cfg is None:
                from ..config import load_config

                cfg = load_config()
            new_root = rerun_session(s, out / "rerun", cfg)
            st = load_stats(new_root, labels)
            st.name = root.name
        else:
            st = load_stats(root, labels)
        stats.append(st)
    data = build_report(stats)
    csv_path, html_path = write_report(data, out)
    return data, csv_path, html_path
