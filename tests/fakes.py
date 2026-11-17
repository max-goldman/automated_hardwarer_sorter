"""Builders for contract types with sensible defaults (shared by stage tests)."""

from __future__ import annotations

import numpy as np

from hwsorter.contracts import (
    Blob,
    Classification,
    FeatureVector,
    FusedFeatures,
    GroupDecision,
    Observation,
    Output,
    PartClass,
    PartResult,
    Prepared,
    ScheduledOutput,
    Track,
    TrackFlag,
    Verdict,
)


def square_contour(cx: float, cy: float, half: float = 10.0) -> np.ndarray:
    return np.array(
        [
            [cx - half, cy - half],
            [cx + half, cy - half],
            [cx + half, cy + half],
            [cx - half, cy + half],
        ],
        np.float32,
    )


def make_blob(cx: float = 100.0, cy: float = 400.0, half: float = 10.0, **kw) -> Blob:
    c = square_contour(cx, cy, half)
    x0, y0 = c.min(axis=0)
    x1, y1 = c.max(axis=0)
    defaults = {
        "contour_px": c,
        "mask_bbox_px": (int(x0), int(y0), int(x1), int(y1)),
        "area_px": float((2 * half) ** 2),
        "touches_edge": False,
    }
    return Blob(**{**defaults, **kw})


def make_track(
    track_id: int = 1,
    belt_x_lead_in: float = 1.0,
    belt_x_trail_in: float = 0.5,
    n_obs: int = 3,
    flags: TrackFlag = TrackFlag(0),  # noqa: B008
    finished: bool = True,
    **kw,
) -> Track:
    half = (belt_x_lead_in - belt_x_trail_in) / 2
    obs = [
        Observation(
            seq=i,
            t_capture_ns=i * 8_333_333,
            blob=make_blob(),
            contour_in=square_contour((belt_x_lead_in + belt_x_trail_in) / 2, 0.0, half),
        )
        for i in range(n_obs)
    ]
    defaults = {
        "observations": obs,
        "flags": flags,
        "belt_x_lead_in": belt_x_lead_in,
        "belt_x_trail_in": belt_x_trail_in,
        "y_min_in": -0.25,
        "y_max_in": 0.25,
        "t_first_ns": 0,
        "t_last_ns": max(n_obs - 1, 0) * 8_333_333,
        "finished": finished,
    }
    return Track(track_id=track_id, **{**defaults, **kw})


def make_fused(n_frames: int = 5, on_seam: bool = False, **median: float) -> FusedFeatures:
    med = {"area_in2": 0.2, "length_in": 0.6, "width_in": 0.4, **median}
    return FusedFeatures(
        median=med, spread={k: 0.0 for k in med}, n_frames=n_frames, on_seam=on_seam
    )


def make_classification(
    verdict: Verdict = Verdict.CONFIDENT,
    part_class: PartClass | None = PartClass.BOLT_025,
    **kw,
) -> Classification:
    if verdict is not Verdict.CONFIDENT:
        part_class = None
    if verdict is Verdict.CONFIDENT:
        score, runner_up = 0.3, 1.4
    elif verdict is Verdict.OUT_OF_SCOPE:
        score, runner_up = 2.0, 3.0  # best score above out_of_scope_threshold (1.5)
    else:
        score, runner_up = 1.2, 1.3  # in between: neither fits nor clearly out
    defaults = {
        "pose": "side" if part_class else None,
        "score": score,
        "runner_up_score": runner_up,
    }
    return Classification(verdict=verdict, part_class=part_class, **{**defaults, **kw})


def make_part_result(
    track_id: int = 1,
    verdict: Verdict = Verdict.CONFIDENT,
    part_class: PartClass | None = PartClass.BOLT_025,
    belt_x_lead_in: float = 1.0,
    belt_x_trail_in: float = 0.5,
    t_ref_ns: int = 0,
    x_lead_in_at_ref: float = 2.0,
    flags: TrackFlag = TrackFlag(0),  # noqa: B008
    fused: FusedFeatures | None = None,
    classification: Classification | None = None,
    **kw,
) -> PartResult:
    return PartResult(
        track_id=track_id,
        fused=fused if fused is not None else make_fused(),
        classification=classification or make_classification(verdict, part_class),
        flags=flags,
        belt_x_lead_in=belt_x_lead_in,
        belt_x_trail_in=belt_x_trail_in,
        t_ref_ns=t_ref_ns,
        x_lead_in_at_ref=x_lead_in_at_ref,
        **kw,
    )


def make_prepared(seq: int = 0, t_capture_ns: int = 0, h: int = 80, w: int = 128, **kw) -> Prepared:
    from hwsorter.calibration.store import RoiCalibration

    defaults = {
        "flat": np.ones((h, w), np.float32),
        "roi": RoiCalibration(top=0, bottom=h, left=0, right=w, source="auto"),
        "gain": 1.0,
    }
    return Prepared(seq=seq, t_capture_ns=t_capture_ns, **{**defaults, **kw})


def make_observation(seq: int = 0, t_capture_ns: int = 0, cx_in: float = 0.0, **kw) -> Observation:
    defaults = {
        "blob": make_blob(),
        "contour_in": square_contour(cx_in, 0.0, 0.25),
        "valid": True,
    }
    return Observation(seq=seq, t_capture_ns=t_capture_ns, **{**defaults, **kw})


def make_feature_vector(valid: bool = True, **values: float) -> FeatureVector:
    vals = {"area_in2": 0.2, "length_in": 0.6, "width_in": 0.4, **values}
    return FeatureVector(values=vals, valid=valid)


def make_group_decision(
    group_id: int = 1,
    member_track_ids: tuple[int, ...] = (1,),
    output: Output = Output.BOLT_025,
    rule: str = "lone_confident",
    t_ref_ns: int = 0,
    x_lead_in_at_ref: float = 2.0,
    t_closed_ns: int = 1_000_000,
) -> GroupDecision:
    return GroupDecision(
        group_id, member_track_ids, output, rule, t_ref_ns, x_lead_in_at_ref, t_closed_ns
    )


def make_scheduled_output(
    group_id: int = 1,
    output: Output = Output.BOLT_025,
    member_track_ids: tuple[int, ...] = (1,),
    t_send_ns: int = 2_000_000,
) -> ScheduledOutput:
    return ScheduledOutput(group_id, output, member_track_ids, t_send_ns)
