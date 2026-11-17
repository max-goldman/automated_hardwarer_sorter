"""Exposure checks: motion blur, belt brightness and lighting flicker."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import ExposureConfig


def motion_blur_px(exposure_ms: float, speed_in_s: float, px_per_in: float) -> float:
    """How far a part moves, in pixels, while the shutter is open."""
    return exposure_ms / 1000.0 * speed_in_s * px_per_in


def max_exposure_ms(max_blur_px: float, speed_in_s: float, px_per_in: float, step_ms: float = 0.1) -> float:
    """Longest exposure (rounded down to the camera's step) that keeps blur within the limit."""
    exact = max_blur_px / (speed_in_s * px_per_in) * 1000.0
    return max(np.floor(exact / step_ms + 1e-9) * step_ms, step_ms)


@dataclass
class FlickerStats:
    swing_pct: float       # (plateau - deepest dip) / plateau, percent
    dipped_pct: float      # share of frames noticeably below the plateau, percent
    plateau: float         # normal belt brightness (gray level)
    frames: int


def flicker_stats(levels: np.ndarray) -> FlickerStats:
    """Frame-to-frame brightness stability from per-frame mean gray levels."""
    levels = np.asarray(levels, np.float64)
    if len(levels) < 10:
        return FlickerStats(0.0, 0.0, float(levels.mean()) if len(levels) else 0.0, len(levels))
    plateau = float(np.percentile(levels, 90))
    if plateau <= 1e-6:
        return FlickerStats(0.0, 0.0, plateau, len(levels))
    swing = (plateau - float(levels.min())) / plateau * 100.0
    dipped = float((levels < 0.97 * plateau).mean() * 100.0)
    return FlickerStats(swing, dipped, plateau, len(levels))


def exposure_advice(exposure_ms: float, belt_level: float, flicker: FlickerStats, blur_px: float,
                    recommended_ms: float, saturated_pct: float, cfg: ExposureConfig) -> list[str]:
    """Plain-language findings, most important first. An empty list means all is well."""
    notes: list[str] = []
    if blur_px > cfg.max_blur_px + 1e-6:
        notes.append(f"Exposure is too long: parts smear {blur_px:.1f} px at belt speed (limit {cfg.max_blur_px:g} px). "
                     f"Use {recommended_ms:.1f} ms or less.")
    if flicker.swing_pct > cfg.flicker_warn_pct:
        notes.append(f"Lighting flickers: {flicker.dipped_pct:.0f}% of frames are dimmer than normal, the worst by "
                     f"{flicker.swing_pct:.0f}%. Use lights without PWM dimming (constant current), or run the dimmer at 100%.")
    if saturated_pct > 0.5:
        notes.append(f"{saturated_pct:.1f}% of the image is saturated. Reduce the light or shorten the exposure.")
    if belt_level < cfg.belt_min_level:
        notes.append(f"Not enough light: the belt reads {belt_level:.0f} of 255 (aim for about {cfg.belt_target_level}). "
                     "Add light; the camera's gain control does not work, so brightness must come from light or exposure.")
    elif belt_level < 0.8 * cfg.belt_target_level:
        notes.append(f"The belt reads {belt_level:.0f} of 255; more light would help (aim for about {cfg.belt_target_level}).")
    return notes
