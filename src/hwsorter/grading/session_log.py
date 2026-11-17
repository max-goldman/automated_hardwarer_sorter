"""Load a session folder: the decision-log JSONL plus the crop and full-frame image paths.

Layout (written by the pipeline): ``<session>/decisions/decision_log.jsonl`` and
``<session>/crops/part_NNNN.png`` / ``part_NNNN_frame.png``; ``crop_ref``/``frame_ref`` in a part
record are relative to the session folder. A bare log path (``sorter run --decision-log X``)
puts the crops beside the log; both layouts resolve. Only the last ``session_start`` in a log
is used, so an appended re-run does not mix with the earlier one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

LOG_NAME = "decision_log.jsonl"


@dataclass(frozen=True)
class LoggedPart:
    """One ``part`` record: the pipeline's view of a finished track."""

    track_id: int
    verdict: str  # confident | out_of_scope | unresolved
    part_class: str | None  # only when confident
    score: float | None
    flags: tuple[str, ...]
    t_ref_ns: int
    x_lead_in_at_ref: float
    belt_x_lead_in: float
    belt_x_trail_in: float
    crop_ref: str | None
    frame_ref: str | None
    raw: dict[str, Any] = field(repr=False, compare=False, default_factory=dict)


@dataclass(frozen=True)
class LoggedGroup:
    """One ``group`` record joined with its ``send`` record (if any)."""

    group_id: int
    member_track_ids: tuple[int, ...]
    output: str  # the decided output
    rule: str
    t_ref_ns: int
    sent_output: str | None = None  # from the send record; MISC when the decision was late
    miss: str | None = None


@dataclass
class SessionData:
    root: Path
    log_path: Path
    start: dict[str, Any]
    end: dict[str, Any] | None
    parts: list[LoggedPart]  # ordered by (t_ref_ns, belt_x_lead_in, track_id)
    groups: list[LoggedGroup]
    _by_track: dict[int, int] = field(default_factory=dict, repr=False)

    def part_index(self, track_id: int) -> int | None:
        return self._by_track.get(track_id)

    def group_of(self, track_id: int) -> LoggedGroup | None:
        for g in self.groups:
            if track_id in g.member_track_ids:
                return g
        return None

    def image_path(self, ref: str | None) -> Path | None:
        """Absolute path of a crop/frame ref if the file exists, else None."""
        if not ref:
            return None
        for base in (self.root, self.log_path.parent):
            p = base / ref
            if p.is_file():
                return p
        return None

    def crop_path(self, part: LoggedPart) -> Path | None:
        return self.image_path(part.crop_ref)

    def frame_path(self, part: LoggedPart) -> Path | None:
        return self.image_path(part.frame_ref)


def find_log(path: str | Path) -> tuple[Path, Path]:
    """Resolve a session folder or a decision-log file to ``(session root, log path)``."""
    p = Path(path)
    if p.is_file():
        log = p
        root = p.parent.parent if p.parent.name == "decisions" else p.parent
        return root, log
    if not p.is_dir():
        raise FileNotFoundError(f"no such session folder or decision log: {p}")
    for cand in (p / "decisions" / LOG_NAME, p / LOG_NAME):
        if cand.is_file():
            return p, cand
    found = sorted((p / "decisions").glob("*.jsonl")) or sorted(p.glob("*.jsonl"))
    found = [f for f in found if not f.name.startswith("labels")]
    if found:
        return p, found[0]
    raise FileNotFoundError(f"no decision log found under {p}")


def _num(v: Any, default: float = 0.0) -> float:
    return float(v) if isinstance(v, (int, float)) else default


def _to_part(r: dict[str, Any]) -> LoggedPart:
    score = r.get("score")
    return LoggedPart(
        track_id=int(r["track_id"]),
        verdict=str(r.get("verdict", "unresolved")),
        part_class=r.get("part_class"),
        score=float(score) if isinstance(score, (int, float)) else None,
        flags=tuple(r.get("flags") or ()),
        t_ref_ns=int(r.get("t_ref_ns", 0)),
        x_lead_in_at_ref=_num(r.get("x_lead_in_at_ref")),
        belt_x_lead_in=_num(r.get("belt_x_lead_in")),
        belt_x_trail_in=_num(r.get("belt_x_trail_in")),
        crop_ref=r.get("crop_ref"),
        frame_ref=r.get("frame_ref"),
        raw=r,
    )


def load_session(path: str | Path) -> SessionData:
    root, log_path = find_log(path)
    start: dict[str, Any] = {}
    end: dict[str, Any] | None = None
    parts: list[LoggedPart] = []
    groups: dict[int, LoggedGroup] = {}
    sends: dict[int, dict[str, Any]] = {}
    with log_path.open(encoding="utf-8") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            try:
                r = json.loads(raw)
            except json.JSONDecodeError:
                continue  # a torn last line from a live session
            kind = r.get("kind")
            if kind == "session_start":
                start, end, parts, groups, sends = r, None, [], {}, {}
            elif kind == "session_end":
                end = r
            elif kind == "part":
                parts.append(_to_part(r))
            elif kind == "group":
                gid = int(r["group_id"])
                groups[gid] = LoggedGroup(
                    group_id=gid,
                    member_track_ids=tuple(int(t) for t in r.get("member_track_ids", ())),
                    output=str(r.get("output")),
                    rule=str(r.get("rule", "")),
                    t_ref_ns=int(r.get("t_ref_ns", 0)),
                )
            elif kind == "send":
                sends[int(r["group_id"])] = r
    joined = []
    for gid, group in groups.items():
        s = sends.get(gid)
        sent = (
            None if s is None else replace(group, sent_output=s.get("output"), miss=s.get("miss"))
        )
        joined.append(sent or group)
    parts.sort(key=lambda p: (p.t_ref_ns, p.belt_x_lead_in, p.track_id))
    joined.sort(key=lambda g: g.group_id)
    data = SessionData(root, log_path, start, end, parts, joined)
    data._by_track = {p.track_id: i for i, p in enumerate(parts)}
    return data
