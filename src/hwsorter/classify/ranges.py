"""Range classifier: worst-feature fit per part+pose, verdicts per contracts invariant 7."""

from __future__ import annotations

import math
from typing import Protocol

from hwsorter.classify.library import PartsLibrary
from hwsorter.config import ClassifyConfig
from hwsorter.contracts import Classification, FusedFeatures, PartClass, TrackFlag, Verdict

_CLAMP = 1e6
_NO_FLAGS = TrackFlag(0)


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
        """Worst-feature score (0 centre, 1 range edge, >1 outside).

        NaN rule (one, consistent): a feature the pose requires whose fused value is NaN or
        missing means "unavailable", so the pose cannot be shown to fit: it scores inf (and that
        feature is the worst feature). NaN never passes as a fit and never raises. Features the
        pose does not list are ignored, so NaNs in unrelated features (e.g. bolt-only
        features on a nut) do no harm.
        """
        worst, worst_name = 0.0, None
        for name, (lo, hi) in ranges.items():
            v = fused.median.get(name, math.nan)
            if not math.isfinite(v):
                return math.inf, name
            s = abs(v - (lo + hi) / 2.0) / ((hi - lo) / 2.0)
            if worst_name is None or s > worst:
                worst, worst_name = s, name
        return worst, worst_name

    def classify(self, fused: FusedFeatures, flags: TrackFlag = _NO_FLAGS) -> Classification:
        cfg = self.cfg
        best: dict[PartClass, tuple[float, str, str | None]] = {}
        fitting: list[tuple[PartClass, str]] = []
        for pc, spec in self.library.parts.items():
            for pose, ranges in spec.poses.items():
                score, wf = self.score_pose(ranges, fused)
                if score <= cfg.confidence_threshold:
                    fitting.append((pc, pose))
                if pc not in best or score < best[pc][0]:
                    best[pc] = (score, pose, wf)
        ranked = sorted(best.items(), key=lambda kv: kv[1][0])
        _, (score, pose, wf) = ranked[0]
        runner_up = ranked[1][1][0] if len(ranked) > 1 else math.inf

        def result(verdict: Verdict, pc: PartClass | None = None) -> Classification:
            # inf (unscorable) is clamped to a finite value so decision logs stay valid JSON.
            return Classification(
                verdict=verdict,
                part_class=pc,
                pose=pose if math.isfinite(score) else None,
                score=min(score, _CLAMP),
                runner_up_score=min(runner_up, _CLAMP),
                worst_feature=wf,
            )

        if TrackFlag.MERGED in flags or not math.isfinite(score):
            return result(Verdict.UNRESOLVED)
        if score > cfg.out_of_scope_threshold:
            return result(Verdict.OUT_OF_SCOPE)
        if (
            len(fitting) == 1  # exactly one pose of one part fits
            and fused.n_frames >= cfg.min_frames
            and TrackFlag.UNSETTLED not in flags
        ):
            return result(Verdict.CONFIDENT, fitting[0][0])
        return result(Verdict.UNRESOLVED)
