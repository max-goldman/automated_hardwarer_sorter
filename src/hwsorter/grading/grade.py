"""Grading: join labels with the measured grouping and compute the correct output per group.

The grouping is the pipeline's (``group`` records), the identities are the labels', and the
correct output is ``decide.rules.correct_output`` over the labels of the group's members
(a several-parts label contributes one identity per part; ``excluded`` members contribute none).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts import Output, PartClass
from ..decide.rules import correct_output
from .labels import (
    KIND_CLASS,
    KIND_EXCLUDED,
    KIND_MULTI,
    KIND_OUT_OF_SCOPE,
    Label,
    LabelStore,
    Rematch,
)
from .session_log import LoggedGroup, LoggedPart, SessionData

CORRECT, WRONG, INCOMPLETE, PHANTOM = "correct", "wrong", "incomplete", "phantom"


@dataclass(frozen=True)
class GradedGroup:
    group_id: int
    member_track_ids: tuple[int, ...]
    predicted: Output  # the decided output
    sent: Output | None  # what was actually sent (MISC on a late decision), if logged
    correct: Output | None  # None when incomplete or phantom
    status: str  # correct | wrong | incomplete (a member unlabelled) | phantom (all excluded)

    @property
    def sent_correct(self) -> bool | None:
        if self.correct is None or self.sent is None:
            return None
        return self.sent == self.correct


@dataclass
class GradeReport:
    groups: list[GradedGroup] = field(default_factory=list)
    parts_fed: int | None = None
    n_detected_parts: int = 0  # real parts accounted for by detections (labels; unlabelled = 1)
    n_undetected: int | None = None  # parts_fed - n_detected_parts, floored at 0 (None if unset)
    n_overcount: int = 0  # n_detected_parts above parts_fed: labels or parts_fed are off
    n_phantom_parts: int = 0  # detections labelled excluded
    n_unlabeled_parts: int = 0
    unmatched_labels: list[Label] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for g in self.groups if g.status == status)

    @property
    def complete(self) -> bool:
        return self.n_unlabeled_parts == 0

    @property
    def n_misses(self) -> int:
        """Wrong groups plus phantom groups plus undetected parts (each is a sort failure)."""
        return self.count(WRONG) + self.count(PHANTOM) + (self.n_undetected or 0)

    @property
    def accuracy(self) -> float | None:
        """Correct groups over every graded group plus undetected parts; None if nothing graded."""
        graded = self.count(CORRECT) + self.count(WRONG) + self.count(PHANTOM)
        denom = graded + (self.n_undetected or 0)
        return self.count(CORRECT) / denom if denom else None


def _correct_for(group: LoggedGroup, labels: list[Label | None]) -> tuple[Output | None, str]:
    if any(lab is None for lab in labels):
        return None, INCOMPLETE
    idents: list[PartClass | None] = []
    for lab in labels:
        assert lab is not None
        idents.extend(lab.resolved())
    if not idents:
        return None, PHANTOM
    return correct_output(idents), CORRECT  # CORRECT is refined by the caller


def grade_session(
    session: SessionData, store: LabelStore, match: Rematch | None = None
) -> GradeReport:
    match = match if match is not None else store.rematch(session.parts)
    part_label: dict[int, Label | None] = {i: None for i in range(len(session.parts))}
    for key, i in match.matched.items():
        part_label[i] = store.get(key)
    by_track = {p.track_id: part_label[i] for i, p in enumerate(session.parts)}

    rep = GradeReport(parts_fed=store.parts_fed, unmatched_labels=list(match.unmatched_labels))
    for g in session.groups:
        labels = [by_track.get(t) for t in g.member_track_ids]
        correct, status = _correct_for(g, labels)
        predicted = Output(g.output)
        sent = Output(g.sent_output) if g.sent_output else None
        if status == CORRECT:
            status = CORRECT if correct == predicted else WRONG
        rep.groups.append(
            GradedGroup(g.group_id, g.member_track_ids, predicted, sent, correct, status)
        )
    for lab in part_label.values():
        if lab is None:
            rep.n_unlabeled_parts += 1
            rep.n_detected_parts += 1
        else:
            rep.n_detected_parts += lab.n_parts
            rep.n_phantom_parts += lab.kind == KIND_EXCLUDED
    if store.parts_fed is not None:
        rep.n_undetected = max(0, store.parts_fed - rep.n_detected_parts)
        rep.n_overcount = max(0, rep.n_detected_parts - store.parts_fed)
    return rep


class GradingSession:
    """A loaded session plus its label store: what the UI edits and the report reads."""

    def __init__(self, session: SessionData, store: LabelStore) -> None:
        self.session = session
        self.store = store
        self.match = store.rematch(session.parts)

    @property
    def parts(self) -> list[LoggedPart]:
        return self.session.parts

    def __len__(self) -> int:
        return len(self.session.parts)

    def label_of(self, index: int) -> Label | None:
        key = self.match.part_to_key().get(index)
        return self.store.get(key) if key else None

    def set_label(
        self,
        index: int,
        kind: str,
        part_class: str | None = None,
        identities: tuple[str | None, ...] = (),
    ) -> Label:
        """Label part ``index`` (replacing any label it has) and save at once."""
        part = self.parts[index]
        existing = self.label_of(index)
        # Relabelling keeps the original anchor/key so the label stays matched to the part.
        key = existing.key if existing else self.store.new_key(part.t_ref_ns, part.belt_x_lead_in)
        anchor = existing or part
        lab = Label(
            key=key,
            t_ref_ns=anchor.t_ref_ns,
            belt_x_lead_in=anchor.belt_x_lead_in,
            belt_x_trail_in=anchor.belt_x_trail_in,
            kind=kind,
            track_id=part.track_id,
            part_class=part_class if kind == KIND_CLASS else None,
            identities=tuple(identities) if kind == KIND_MULTI else (),
        )
        self.store.put(lab)
        self.match = self.store.rematch(self.parts)
        return lab

    def clear_label(self, index: int) -> None:
        lab = self.label_of(index)
        if lab is not None:
            self.store.remove(lab.key)
            self.match = self.store.rematch(self.parts)

    def set_parts_fed(self, n: int | None) -> None:
        self.store.set_parts_fed(n)

    def predicted_label_args(self, index: int) -> tuple[str, str | None] | None:
        """(kind, part_class) the pipeline predicted, for Enter-to-confirm; None if unresolved."""
        p = self.parts[index]
        if p.verdict == "confident" and p.part_class:
            return KIND_CLASS, p.part_class
        if p.verdict == "out_of_scope":
            return KIND_OUT_OF_SCOPE, None
        return None

    def report(self) -> GradeReport:
        return grade_session(self.session, self.store, self.match)
