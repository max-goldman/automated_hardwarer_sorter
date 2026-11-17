"""Range classifier: worst-feature fit per part+pose, verdicts per contracts invariant 7."""

from __future__ import annotations

import math
from typing import Protocol

from hwsorter.classify.library import PartsLibrary
from hwsorter.config import ClassifyConfig
from hwsorter.contracts import Classification, FusedFeatures, PartClass, TrackFlag, Verdict


class Classifier(Protocol):
    def classify(self, fused: FusedFeatures, flags: TrackFlag) -> Classification: ...


class RangeClassifier:
    def __init__(self, library: PartsLibrary, cfg: ClassifyConfig | None = None) -> None:
        self.library = library
        self.cfg = cfg or ClassifyConfig()

    @staticmethod
    def score_pose(
        ranges: dict[str, tuple[float, float]], fused: FusedFeatures
    ) -> tuple[float, str | None]:
        """Worst-feature score (0 centre, 1 range edge). NaN or missing features are skipped.
        Fewer than half the pose's features applicable gives inf (not scorable)."""
        worst, worst_name, used = 0.0, None, 0
        for name, (lo, hi) in ranges.items():
            v = fused.median.get(name, math.nan)
            if not math.isfinite(v):
                continue
            used += 1
            s = abs(v - (lo + hi) / 2.0) / ((hi - lo) / 2.0)
            if worst_name is None or s > worst:
                worst, worst_name = s, name
        if used == 0 or used * 2 < len(ranges):
            return math.inf, None
        return worst, worst_name

    def classify(self, fused: FusedFeatures, flags: TrackFlag = TrackFlag(0)) -> Classification:
        best: dict[PartClass, tuple[float, str, str | None]] = {}
        for pc, spec in self.library.parts.items():
            for pose, ranges in spec.poses.items():
                score, wf = self.score_pose(ranges, fused)
                if pc not in best or score < best[pc][0]:
                    best[pc] = (score, pose, wf)
        ranked = sorted(best.items(), key=lambda kv: kv[1][0])
        top_pc, (score, pose, wf) = ranked[0]
        runner_up = ranked[1][1][0] if len(ranked) > 1 else math.inf
        cfg = self.cfg

        def result(verdict: Verdict, pc: PartClass | None = None) -> Classification:
            return Classification(
                verdict=verdict,
                part_class=pc,
                pose=pose if math.isfinite(score) else None,
                score=score,
                runner_up_score=runner_up,
                worst_feature=wf,
            )

        if TrackFlag.MERGED in flags or not math.isfinite(score):
            return result(Verdict.UNRESOLVED)
        if score > cfg.out_of_scope_threshold:
            return result(Verdict.OUT_OF_SCOPE)
        if (
            score <= cfg.confidence_threshold
            and runner_up > cfg.confidence_threshold
            and fused.n_frames >= cfg.min_frames
            and TrackFlag.UNSETTLED not in flags
        ):
            return result(Verdict.CONFIDENT, top_pc)
        return result(Verdict.UNRESOLVED)
