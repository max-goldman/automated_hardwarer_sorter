"""Ground-truth label store: an append-only JSON-lines journal, saved on every label action.

File: ``<session>/labels.jsonl`` (one JSON object per line, schema version ``v`` = 1). The file
is a journal: records are applied in order and a later record supersedes an earlier one with the
same ``key``, so correcting a label (going back and relabelling) just appends. Reading replays
the journal, so a session is resumable after a crash or at any keystroke. A torn last line is
ignored.

Record types (``type``):

``label``  one ground-truth label for one detected part::

    {"v":1, "type":"label",
     "key": "<t_ref_ns>:<belt_x_lead_in:.3f>",   # stable id, fixed when first created
     "t_ref_ns": int, "belt_x_lead_in": float, "belt_x_trail_in": float,   # the ANCHOR
     "track_id": int,                 # informational only: NOT stable across pipeline re-runs
     "kind": "class" | "out_of_scope" | "excluded" | "multi",
     "part_class": "bolt_025" | ... | null,       # kind == "class": the PartClass value
     "identities": [ "<PartClass value>" | null, ... ]}   # kind == "multi": one per part, null
                                                          # = out of scope; [] otherwise

``unlabel``  ``{"v":1,"type":"unlabel","key":...}`` removes a label (back to unlabelled).

``run``  ``{"v":1,"type":"run","parts_fed": int | null}`` the number of parts physically fed
in this run (latest record wins). Detected parts are counted from labels; the shortfall is the
undetected parts, and ``excluded`` labels are phantom detections.

Meaning of ``kind``: ``class`` one real in-scope part; ``out_of_scope`` one real part that is
no in-scope class (correct bin MISC); ``excluded`` not a real part (phantom, bad crop, the seam,
debris) and counts as zero parts; ``multi`` the crop holds several real parts, ``identities``
lists them (``null`` = out of scope), so the crop counts as ``len(identities)`` parts.

Anchoring: a label is anchored to ``(t_ref_ns, belt_x_lead_in, belt_x_trail_in)`` of the part it
was made on, never to ``track_id``, so labels survive pipeline re-runs. ``LabelStore.rematch``
pairs stored labels with the parts of a re-run by time and belt-x overlap and reports what did
not pair. Belt-frame x is constant per part (invariant 4), so it is the main discriminator.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..contracts import PartClass

LABEL_SCHEMA_VERSION = 1

KIND_CLASS = "class"
KIND_OUT_OF_SCOPE = "out_of_scope"
KIND_EXCLUDED = "excluded"
KIND_MULTI = "multi"
KINDS = (KIND_CLASS, KIND_OUT_OF_SCOPE, KIND_EXCLUDED, KIND_MULTI)

DEFAULT_T_TOL_NS = 400_000_000  # t_ref may differ by this much between runs
DEFAULT_X_TOL_IN = 0.3  # belt-x intervals may be this far apart and still match


@dataclass(frozen=True)
class Label:
    key: str
    t_ref_ns: int
    belt_x_lead_in: float
    belt_x_trail_in: float
    kind: str
    track_id: int | None = None
    part_class: str | None = None
    identities: tuple[str | None, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown label kind {self.kind!r}")
        if self.kind == KIND_CLASS:
            PartClass(self.part_class)  # raises on a bad value
        for ident in self.identities:
            if ident is not None:
                PartClass(ident)

    def resolved(self) -> list[PartClass | None]:
        """Identities for ``decide.rules.correct_output`` (None = out of scope); [] = no parts."""
        if self.kind == KIND_CLASS:
            return [PartClass(self.part_class)]
        if self.kind == KIND_OUT_OF_SCOPE:
            return [None]
        if self.kind == KIND_MULTI:
            return [None if i is None else PartClass(i) for i in self.identities]
        return []

    @property
    def n_parts(self) -> int:
        return len(self.resolved())

    def to_record(self) -> dict[str, Any]:
        d = asdict(self)
        d["identities"] = list(self.identities)
        return {"v": LABEL_SCHEMA_VERSION, "type": "label", **d}

    @classmethod
    def from_record(cls, r: dict[str, Any]) -> Label:
        return cls(
            key=str(r["key"]),
            t_ref_ns=int(r["t_ref_ns"]),
            belt_x_lead_in=float(r["belt_x_lead_in"]),
            belt_x_trail_in=float(r["belt_x_trail_in"]),
            kind=str(r["kind"]),
            track_id=r.get("track_id"),
            part_class=r.get("part_class"),
            identities=tuple(r.get("identities") or ()),
        )


@dataclass
class Rematch:
    """Result of pairing stored labels with a (possibly re-run) session's parts."""

    matched: dict[str, int] = field(default_factory=dict)  # label key -> part index
    unmatched_labels: list[Label] = field(default_factory=list)  # label with no part now
    unlabeled_parts: list[int] = field(default_factory=list)  # part index with no label

    def part_to_key(self) -> dict[int, str]:
        return {i: k for k, i in self.matched.items()}


def make_key(t_ref_ns: int, belt_x_lead_in: float) -> str:
    return f"{t_ref_ns}:{belt_x_lead_in:.3f}"


class LabelStore:
    """The labels of one session, backed by the journal file (``path=None`` = memory only)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._labels: dict[str, Label] = {}
        self.parts_fed: int | None = None
        if self.path and self.path.is_file():
            self._load()

    # ------------------------------------------------------------------ persistence

    def _load(self) -> None:
        assert self.path is not None
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                r = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = r.get("type")
            if kind == "label":
                lab = Label.from_record(r)
                self._labels.pop(lab.key, None)  # re-insert so order follows the latest write
                self._labels[lab.key] = lab
            elif kind == "unlabel":
                self._labels.pop(str(r.get("key")), None)
            elif kind == "run":
                pf = r.get("parts_fed")
                self.parts_fed = None if pf is None else int(pf)

    def _append(self, record: dict[str, Any]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
            fh.flush()

    # ------------------------------------------------------------------ edits

    def put(self, label: Label) -> None:
        """Insert or replace a label and save immediately."""
        self._labels.pop(label.key, None)
        self._labels[label.key] = label
        self._append(label.to_record())

    def remove(self, key: str) -> None:
        if self._labels.pop(key, None) is not None:
            self._append({"v": LABEL_SCHEMA_VERSION, "type": "unlabel", "key": key})

    def set_parts_fed(self, n: int | None) -> None:
        self.parts_fed = n
        self._append({"v": LABEL_SCHEMA_VERSION, "type": "run", "parts_fed": n})

    # ------------------------------------------------------------------ queries

    def get(self, key: str) -> Label | None:
        return self._labels.get(key)

    def labels(self) -> list[Label]:
        return list(self._labels.values())

    def __len__(self) -> int:
        return len(self._labels)

    def new_key(self, t_ref_ns: int, belt_x_lead_in: float) -> str:
        key = make_key(t_ref_ns, belt_x_lead_in)
        base, n = key, 1
        while key in self._labels:
            n += 1
            key = f"{base}#{n}"
        return key

    # ------------------------------------------------------------------ rematch

    def rematch(
        self,
        parts: Sequence[Any],
        *,
        t_tol_ns: int = DEFAULT_T_TOL_NS,
        x_tol_in: float = DEFAULT_X_TOL_IN,
    ) -> Rematch:
        """Pair stored labels with ``parts`` (anything with ``t_ref_ns``, ``belt_x_lead_in``,
        ``belt_x_trail_in``) one-to-one, nearest first.

        A pair is a candidate when ``|dt_ref| <= t_tol_ns`` and the belt-x intervals overlap
        after widening each by ``x_tol_in``. Cost = normalised time offset + normalised offset
        of the interval centres; the cheapest pairs are taken greedily, so an unchanged session
        matches exactly and a re-run with shifted reference times still pairs by overlap."""
        pairs: list[tuple[float, str, int]] = []
        for lab in self._labels.values():
            lc = (lab.belt_x_lead_in + lab.belt_x_trail_in) / 2
            for i, p in enumerate(parts):
                dt = abs(p.t_ref_ns - lab.t_ref_ns)
                if dt > t_tol_ns:
                    continue
                if (
                    p.belt_x_trail_in - x_tol_in > lab.belt_x_lead_in
                    or lab.belt_x_trail_in - x_tol_in > p.belt_x_lead_in
                ):
                    continue
                pc = (p.belt_x_lead_in + p.belt_x_trail_in) / 2
                cost = dt / t_tol_ns + abs(pc - lc) / x_tol_in
                pairs.append((cost, lab.key, i))
        pairs.sort()
        res = Rematch()
        used_parts: set[int] = set()
        for _cost, key, i in pairs:
            if key in res.matched or i in used_parts:
                continue
            res.matched[key] = i
            used_parts.add(i)
        res.unmatched_labels = [lab for k, lab in self._labels.items() if k not in res.matched]
        res.unlabeled_parts = [i for i in range(len(parts)) if i not in used_parts]
        return res
