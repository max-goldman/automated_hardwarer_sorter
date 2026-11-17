"""Grouping of finished parts and the per-group output decision (brief: decision rules).

Pure and deterministic: no clock, no randomness, no Qt. Positions are belt-frame inches
(``belt_x``, larger = further downstream), so a part's extents never change after it finishes.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..contracts import (
    Classification,
    GroupDecision,
    Output,
    PartClass,
    PartResult,
    Verdict,
)


def correct_output(labels_in_group: Sequence[PartClass | None]) -> Output:
    """Correct output for a group given ground-truth labels (None = out of scope).

    Shared by grading and the report. One part: its class bin, or MISC if out of scope.
    Several: that class's bin if all the same in-scope class, MISC if all out of scope,
    otherwise REWORK.
    """
    if not labels_in_group:
        raise ValueError("a group needs at least one label")
    first = labels_in_group[0]
    if all(label == first for label in labels_in_group):
        return Output.MISC if first is None else Output.for_class(first)
    return Output.REWORK


def _confident_class(c: Classification) -> PartClass | None:
    return c.part_class if c.verdict is Verdict.CONFIDENT else None


def decide_with_rule(members: Sequence[PartResult]) -> tuple[Output, str]:
    """Decision table (invariant 8) returning ``(output, rule)`` with ``rule`` from RULES."""
    if not members:
        raise ValueError("a group needs at least one member")
    if len(members) == 1:
        cls = _confident_class(members[0].classification)
        if cls is not None:
            return Output.for_class(cls), "lone_confident"
        return Output.MISC, "lone_misc"  # out of scope or unresolved; never REWORK
    if all(m.classification.verdict is Verdict.OUT_OF_SCOPE for m in members):
        return Output.MISC, "group_all_oos"
    classes = {_confident_class(m.classification) for m in members}
    if len(classes) == 1 and None not in classes:
        return Output.for_class(next(iter(classes))), "group_same_class"
    return Output.REWORK, "rework"


def decide(members: Sequence[PartResult]) -> Output:
    return decide_with_rule(members)[0]


class Grouper:
    """Collects finished parts and releases them as groups once each can no longer grow.

    Two parts link when ``gap < rework_gap_in`` where gap is the along-x edge-to-edge distance
    clamped at 0 (side by side or overlapping = 0); groups are the transitive closure. A group
    closes when ``entry_belt_x_in <= group_trail - rework_gap_in``: the belt entering the image
    is then a full gap behind it, so no later part can link. The caller guarantees no live
    track lies in that span (pipeline responsibility).
    """

    def __init__(self, rework_gap_in: float) -> None:
        self.rework_gap_in = rework_gap_in
        self._parts: list[PartResult] = []

    def add(self, part: PartResult) -> None:
        self._parts.append(part)

    def pending(self) -> int:
        return len(self._parts)

    def _clusters(self) -> list[list[PartResult]]:
        # Sweep from upstream (low trail) to downstream; a part links to the cluster so far when
        # its trail is within the gap of the cluster's max lead (chain linking is transitive).
        ordered = sorted(self._parts, key=lambda p: (p.belt_x_trail_in, p.track_id))
        clusters: list[list[PartResult]] = []
        reach = 0.0
        for p in ordered:
            if clusters and p.belt_x_trail_in - reach < self.rework_gap_in:
                clusters[-1].append(p)
                reach = max(reach, p.belt_x_lead_in)
            else:
                clusters.append([p])
                reach = p.belt_x_lead_in
        return clusters

    def _release(self, clusters: list[list[PartResult]]) -> list[list[PartResult]]:
        gone = {id(p) for c in clusters for p in c}
        self._parts = [p for p in self._parts if id(p) not in gone]
        # Most downstream group first; members in track-id order.
        out = [sorted(c, key=lambda p: p.track_id) for c in clusters]
        out.sort(key=lambda c: (-max(p.belt_x_lead_in for p in c), c[0].track_id))
        return out

    def advance(self, entry_belt_x_in: float, t_ns: int) -> list[list[PartResult]]:
        """Return the groups that have closed given the belt-frame x of the image entry edge."""
        closed = [
            c
            for c in self._clusters()
            if entry_belt_x_in <= min(p.belt_x_trail_in for p in c) - self.rework_gap_in
        ]
        return self._release(closed)

    def flush(self) -> list[list[PartResult]]:
        """Close every remaining group (end of session)."""
        return self._release(self._clusters())


class Decider:
    """Grouper plus decision table: finished parts in, ``GroupDecision`` out."""

    def __init__(self, rework_gap_in: float) -> None:
        self.grouper = Grouper(rework_gap_in)
        self._next_group_id = 1

    def add(self, part: PartResult) -> None:
        self.grouper.add(part)

    def advance(self, entry_belt_x_in: float, t_ns: int) -> list[GroupDecision]:
        return [self._decide(g, t_ns) for g in self.grouper.advance(entry_belt_x_in, t_ns)]

    def flush(self, t_ns: int) -> list[GroupDecision]:
        return [self._decide(g, t_ns) for g in self.grouper.flush()]

    def _decide(self, members: list[PartResult], t_ns: int) -> GroupDecision:
        output, rule = decide_with_rule(members)
        lead = max(members, key=lambda p: (p.belt_x_lead_in, -p.track_id))
        gid = self._next_group_id
        self._next_group_id += 1
        return GroupDecision(
            group_id=gid,
            member_track_ids=tuple(p.track_id for p in members),
            output=output,
            rule=rule,
            t_ref_ns=lead.t_ref_ns,
            x_lead_in_at_ref=lead.x_lead_in_at_ref,
            t_closed_ns=t_ns,
        )


class StationaryDecider:
    """Decisions for parts that sit still on a stopped belt (``belt.mode = "stationary"``).

    Measured parts come and go (``add`` / ``remove``). ``update`` clusters the parts present with
    the same gap rule as ``Grouper`` and returns a ``GroupDecision`` only for each group whose
    membership is new since the last call, so an untouched group is never decided twice, a group
    that gained or lost a part is re-decided once, and a part's own classification never
    changes while it stays (the pipeline classifies it once)."""

    def __init__(self, rework_gap_in: float) -> None:
        self.rework_gap_in = rework_gap_in
        self._parts: dict[int, PartResult] = {}
        self._decided: dict[tuple[int, ...], GroupDecision] = {}
        self._next_group_id = 1

    def add(self, part: PartResult) -> None:
        self._parts[part.track_id] = part

    def remove(self, track_id: int) -> bool:
        return self._parts.pop(track_id, None) is not None

    def has(self, track_id: int) -> bool:
        return track_id in self._parts

    def current(self) -> list[GroupDecision]:
        """The standing decision of every group now on the belt (decided at least once)."""
        return list(self._decided.values())

    def group_of(self, track_id: int) -> GroupDecision | None:
        for key, d in self._decided.items():
            if track_id in key:
                return d
        return None

    def update(self, t_ns: int) -> list[GroupDecision]:
        grouper = Grouper(self.rework_gap_in)
        for p in self._parts.values():
            grouper.add(p)
        clusters = grouper.flush()  # most downstream first, members in track-id order
        keys = {tuple(p.track_id for p in c) for c in clusters}
        self._decided = {k: d for k, d in self._decided.items() if k in keys}
        out: list[GroupDecision] = []
        for members in sorted(clusters, key=lambda c: c[0].track_id):
            key = tuple(p.track_id for p in members)
            if key in self._decided:
                continue
            output, rule = decide_with_rule(members)
            lead = max(members, key=lambda p: (p.belt_x_lead_in, -p.track_id))
            d = GroupDecision(
                group_id=self._next_group_id,
                member_track_ids=key,
                output=output,
                rule=rule,
                t_ref_ns=lead.t_ref_ns,
                x_lead_in_at_ref=lead.x_lead_in_at_ref,
                t_closed_ns=t_ns,
            )
            self._next_group_id += 1
            self._decided[key] = d
            out.append(d)
        return out
