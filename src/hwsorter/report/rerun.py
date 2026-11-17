"""``--rerun``: replay a graded recording through the current pipeline into a fresh session.

This is a full pipeline replay (ManualClock + mock adapter), not the stored-feature fast path.
The labels stay with the original session; they re-pair with the new parts by time and belt-x
(``LabelStore.rematch``), so a recording replayed with the same timestamps keeps its labels.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..grading import load_session


class RerunError(RuntimeError):
    """The session cannot be re-run (its source recording is unknown or missing)."""


def source_spec(start: dict[str, Any], cfg: Any) -> str:
    from ..cli import resolve_recording

    src = str(start.get("source") or "")
    if not src.startswith("replay:"):
        raise RerunError(f"cannot re-run: session source {src!r} is not a recording")
    try:
        return str(resolve_recording(cfg, src.removeprefix("replay:")))
    except FileNotFoundError as e:
        raise RerunError(str(e)) from e


def rerun_session(session_path: str | Path, out_dir: str | Path, cfg: Any) -> Path:
    """Re-run the recording of ``session_path``; returns the new session folder."""
    from ..clock import ManualClock
    from ..eventlog import EventLog
    from ..output import MockSerialAdapter
    from ..pipeline import build_pipeline, drive, open_run_source

    old = load_session(session_path)
    spec = source_spec(old.start, cfg)
    root = Path(out_dir) / f"{old.root.name}-rerun"
    log_path = root / cfg.pipeline.decision_log_dir / "decision_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.unlink(missing_ok=True)
    log = EventLog(log_path)
    source = open_run_source(cfg, spec, realtime=False)
    pipeline = build_pipeline(cfg, source, ManualClock(0), MockSerialAdapter(), log, root)
    source.start()
    try:
        drive(source, pipeline)
    finally:
        source.stop()
    pipeline.close()
    log.close()
    return root
