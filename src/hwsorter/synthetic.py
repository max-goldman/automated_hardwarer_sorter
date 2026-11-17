"""Synthetic belt frames: rendered parts of known size moving on a textured belt.

Used to develop and test without the rig, and by unit tests that need exact ground truth.
The look copies the hard cases in the real samples: soft shadows, belt specks, dark belt
edges, a diagonal stitched seam, bolt-head facets as bright as the belt, dark nut bores
and see-through washer holes. Dimensions are the nominal sizes from the project brief.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .frames import EndOfStream, Frame, FrameSource, SourceInfo, SourceStats

# Nominal dimensions in inches (project brief, "Parts in scope").
PART_DIMS: dict[str, dict[str, float]] = {
    "bolt_025": {"shank": 0.250, "head_w": 0.505, "head_h": 0.156, "length": 1.00, "tpi": 20},
    "bolt_0375": {"shank": 0.375, "head_w": 0.650, "head_h": 0.234, "length": 1.00, "tpi": 16},
    "nut_025": {"af": 0.438, "bore": 0.201},
    "nut_0375": {"af": 0.563, "bore": 0.313},
    "washer_025": {"od": 0.625, "id": 0.281},
    "washer_0375": {"od": 0.812, "id": 0.406},
    # Out-of-scope items for tests.
    "coin": {"od": 0.705},
    "washer_0312": {"od": 0.688, "id": 0.344},
}
PART_KINDS = tuple(k for k in PART_DIMS if k not in ("coin", "washer_0312"))


@dataclass
class PartSpec:
    kind: str
    t_enter_s: float  # time the part's centre crosses the left image edge
    y_in: float  # centre position across the belt, inches from the belt's top edge
    angle_deg: float = 0.0


@dataclass
class SceneConfig:
    width: int = 1280
    height: int = 800
    fps: float = 120.0
    px_per_in: float = 367.0
    speed_in_s: float = 4.0
    belt_top_px: int = 50  # dark band above the belt
    belt_bottom_px: int = 785  # thin dark band below
    belt_level: float = 185.0
    blur_sigma_px: float = 1.1  # focus softness
    noise_sd: float = 1.6
    seam: bool = True
    loop_length_in: float = 52.0
    seam_at_in: float = 6.0  # seam position along the loop
    specks: int = 4  # belt specks per view length
    flicker_depth: float = 0.0  # 0..1 brightness dip, to mimic the measured lighting flicker
    flicker_every: int = 20  # frames between dips
    seed: int = 1


def _render_part(
    kind: str, angle_deg: float, ppi: float, ss: int = 4
) -> tuple[np.ndarray, np.ndarray]:
    """Return (gray, alpha) float32 patches at 1x, centred on the part."""
    d = PART_DIMS[kind]
    s = ppi * ss
    extent_in = d.get("od") or (d["af"] * 1.2 if "af" in d else d["length"] + d["head_h"])
    size = int(extent_in * s * 1.25) + 8 * ss
    size += size % 2
    gray = np.zeros((size, size), np.float32)
    mask = np.zeros((size, size), np.uint8)
    c = size / 2.0

    def poly(points: list[tuple[float, float]], value: float, on_mask: bool = True) -> None:
        pts = np.round(np.array(points) * s + c).astype(np.int32)
        cv2.fillPoly(gray, [pts], float(value), lineType=cv2.LINE_AA)
        if on_mask:
            cv2.fillPoly(mask, [pts], 255, lineType=cv2.LINE_AA)

    def disc(radius_in: float, value: float, on_mask: bool = True, clear: bool = False) -> None:
        centre, r = (round(c), round(c)), round(radius_in * s)
        cv2.circle(gray, centre, r, float(value), -1, lineType=cv2.LINE_AA)
        if clear:
            cv2.circle(mask, centre, r, 0, -1, lineType=cv2.LINE_AA)
        elif on_mask:
            cv2.circle(mask, centre, r, 255, -1, lineType=cv2.LINE_AA)

    if kind.startswith("washer") or kind == "coin":
        level = {"washer_025": 72.0, "washer_0375": 112.0}.get(kind, 95.0)
        disc(d["od"] / 2, level)
        if "id" in d:
            disc(d["id"] / 2, 0.0, clear=True)  # hole: belt shows through
    elif kind.startswith("nut"):
        r_corner = d["af"] / math.sqrt(3.0)
        hexagon = [
            (r_corner * math.cos(math.radians(60 * k)), r_corner * math.sin(math.radians(60 * k)))
            for k in range(6)
        ]
        poly(hexagon, 135.0)  # chamfered rim, lighter
        disc(d["af"] / 2 * 0.93, 88.0, on_mask=False)  # face
        disc(d["bore"] / 2 * 1.25, 120.0, on_mask=False)  # countersink ring
        disc(d["bore"] / 2, 38.0, on_mask=False)  # dark threaded bore
    elif kind.startswith("bolt"):
        length, shank, head_w, head_h = d["length"], d["shank"], d["head_w"], d["head_h"]
        total = length + head_h
        x0 = -total / 2  # head end
        # Shank with a cylindrical highlight and thread ridges (zig-zag edge).
        pitch = 1.0 / d["tpi"]
        n = int(length / pitch)
        depth = 0.6 * pitch
        top, bottom = [], []
        for k in range(n + 1):
            x = x0 + head_h + k * pitch
            top += [(x, -shank / 2 + depth), (min(x + pitch / 2, total / 2), -shank / 2)]
            bottom += [(x, shank / 2 - depth), (min(x + pitch / 2, total / 2), shank / 2)]
        poly(top + bottom[::-1], 70.0)
        yy = (np.arange(size, dtype=np.float32) - c) / s
        profile = 70.0 + 140.0 * np.exp(-((yy / (shank * 0.22)) ** 2))
        stripes = 1.0 + 0.22 * np.sin(
            2 * np.pi * ((np.arange(size, dtype=np.float32) - c) / s) / pitch
        )
        shaded = profile[:, None] * stripes[None, :]
        inside = mask > 0
        gray[inside] = shaded[inside]
        # Head: dark block with one facet as bright as the belt (the hard case in the samples).
        poly(
            [
                (x0, -head_w / 2),
                (x0 + head_h, -head_w / 2),
                (x0 + head_h, head_w / 2),
                (x0, head_w / 2),
            ],
            52.0,
        )
        poly(
            [
                (x0, -head_w / 2),
                (x0 + head_h, -head_w / 2),
                (x0 + head_h, -head_w * 0.22),
                (x0, -head_w * 0.22),
            ],
            178.0,
        )
    else:
        raise ValueError(f"unknown part kind {kind!r}")

    rot = cv2.getRotationMatrix2D((c - 0.5, c - 0.5), -angle_deg, 1.0)
    gray = cv2.warpAffine(gray, rot, (size, size), flags=cv2.INTER_LINEAR)
    alpha = (
        cv2.warpAffine(mask, rot, (size, size), flags=cv2.INTER_LINEAR).astype(np.float32) / 255.0
    )
    out = size // ss
    gray = cv2.resize(gray, (out, out), interpolation=cv2.INTER_AREA)
    alpha = cv2.resize(alpha, (out, out), interpolation=cv2.INTER_AREA)
    return gray, alpha


def _belt_strip(cfg: SceneConfig, rng: np.random.Generator) -> np.ndarray:
    """One full lap of belt texture as float32 multipliers around 1.0, shape (height, loop_px + width + 2)."""
    loop_px = round(cfg.loop_length_in * cfg.px_per_in)
    strip = np.ones((cfg.height, loop_px), np.float32)
    belt_h = cfg.belt_bottom_px - cfg.belt_top_px
    for _ in range(int(cfg.specks * cfg.loop_length_in / 3.5)):
        x, y = (
            int(rng.integers(0, loop_px)),
            int(rng.integers(cfg.belt_top_px + 4, cfg.belt_bottom_px - 4)),
        )
        cv2.circle(
            strip,
            (x, y),
            int(rng.integers(1, 4)),
            float(rng.uniform(0.55, 0.85)),
            -1,
            lineType=cv2.LINE_AA,
        )
    if cfg.seam:
        ppi = cfg.px_per_in
        band = 0.2 * ppi  # seam width
        slope = math.tan(math.radians(50.0))  # seam runs about 50 degrees from the travel axis
        x_top = cfg.seam_at_in * ppi
        length = belt_h / math.sin(math.radians(50.0))
        ux, uy = math.cos(math.radians(50.0)), math.sin(math.radians(50.0))  # along the seam
        nx, ny = -uy, ux  # across the seam
        p0 = np.array([x_top, cfg.belt_top_px], np.float32)
        quad = np.array(
            [
                p0 - band / 2 * np.array([nx, ny]),
                p0 + length * np.array([ux, uy]) - band / 2 * np.array([nx, ny]),
                p0 + length * np.array([ux, uy]) + band / 2 * np.array([nx, ny]),
                p0 + band / 2 * np.array([nx, ny]),
            ]
        )
        cv2.fillPoly(strip, [np.round(quad).astype(np.int32)], 0.93, lineType=cv2.LINE_AA)
        step = 0.055 * ppi
        for k in range(int(length / step)):
            centre = p0 + (k + 0.5) * step * np.array([ux, uy])
            a = centre - band * 0.48 * np.array([nx, ny]) + rng.normal(0, 1.0, 2)
            b = centre + band * 0.48 * np.array([nx, ny]) + rng.normal(0, 1.0, 2)
            cv2.line(
                strip,
                tuple(np.round(a).astype(int)),
                tuple(np.round(b).astype(int)),
                float(rng.uniform(0.62, 0.78)),
                3,
                lineType=cv2.LINE_AA,
            )
            for end in (a, b):
                cv2.circle(
                    strip, tuple(np.round(end).astype(int)), 2, 0.35, -1, lineType=cv2.LINE_AA
                )
        _ = slope
    return np.concatenate([strip, strip[:, : cfg.width + 2]], axis=1)


class SyntheticRenderer:
    """Renders the frame for any time t (seconds). Deterministic for a given config and part list."""

    def __init__(self, cfg: SceneConfig, parts: list[PartSpec]) -> None:
        self.cfg = cfg
        self.parts = sorted(parts, key=lambda p: p.t_enter_s)
        rng = np.random.default_rng(cfg.seed)
        self._strip = _belt_strip(cfg, rng)
        self._loop_px = self._strip.shape[1] - cfg.width - 2
        yy, xx = np.mgrid[0 : cfg.height, 0 : cfg.width].astype(np.float32)
        # Illumination: brighter centre-left, falling off to the right and bottom like the samples.
        illum = (
            1.0
            - 0.16 * ((xx - cfg.width * 0.4) / cfg.width) ** 2
            - 0.10 * ((yy - cfg.height * 0.45) / cfg.height) ** 2
        )
        illum -= 0.07 * (xx / cfg.width)
        self._illum = (illum * cfg.belt_level).astype(np.float32)
        self._bands = np.ones((cfg.height, 1), np.float32)
        self._bands[: cfg.belt_top_px] = 0.07
        self._bands[cfg.belt_bottom_px :] = 0.07
        self._bands = cv2.GaussianBlur(self._bands, (1, 9), 2.0)
        self._noise = rng.normal(0.0, cfg.noise_sd, (16, cfg.height, cfg.width)).astype(np.float32)
        self._patches: dict[tuple[str, float], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._v_px_s = cfg.speed_in_s * cfg.px_per_in

    def _patch(self, part: PartSpec) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        key = (part.kind, round(part.angle_deg, 3))
        if key not in self._patches:
            gray, alpha = _render_part(part.kind, part.angle_deg, self.cfg.px_per_in)
            shadow = cv2.GaussianBlur(alpha, (0, 0), 0.035 * self.cfg.px_per_in)
            self._patches[key] = (gray, alpha, shadow)
        return self._patches[key]

    def part_centre_px(self, part: PartSpec, t_s: float) -> tuple[float, float]:
        return (
            (t_s - part.t_enter_s) * self._v_px_s,
            self.cfg.belt_top_px + part.y_in * self.cfg.px_per_in,
        )

    def visible_parts(self, t_s: float) -> list[PartSpec]:
        margin = 1.6 * self.cfg.px_per_in
        return [
            p
            for p in self.parts
            if -margin < self.part_centre_px(p, t_s)[0] < self.cfg.width + margin
        ]

    def render(self, t_s: float, frame_index: int = 0) -> np.ndarray:
        cfg = self.cfg
        u0 = (-self._v_px_s * t_s) % self._loop_px
        i0 = int(u0)
        frac = float(u0 - i0)
        window = self._strip[:, i0 : i0 + cfg.width + 1]
        belt = cv2.addWeighted(window[:, :-1], 1.0 - frac, window[:, 1:], frac, 0.0)
        frame = belt * self._illum
        for part in self.visible_parts(t_s):
            gray, alpha, shadow = self._patch(part)
            cx, cy = self.part_centre_px(part, t_s)
            size = gray.shape[0]
            x_f, y_f = cx - size / 2.0, cy - size / 2.0
            xi, yi = math.floor(x_f), math.floor(y_f)
            shift = np.float32([[1, 0, x_f - xi], [0, 1, y_f - yi]])
            dims = (size + 1, size + 1)
            g = cv2.warpAffine(gray, shift, dims, flags=cv2.INTER_LINEAR)
            a = cv2.warpAffine(alpha, shift, dims, flags=cv2.INTER_LINEAR)
            sh = cv2.warpAffine(shadow, shift, dims, flags=cv2.INTER_LINEAR)
            x1, y1, x2, y2 = (
                max(xi, 0),
                max(yi, 0),
                min(xi + size + 1, cfg.width),
                min(yi + size + 1, cfg.height),
            )
            if x1 >= x2 or y1 >= y2:
                continue
            ps = (slice(y1 - yi, y2 - yi), slice(x1 - xi, x2 - xi))
            region = frame[y1:y2, x1:x2]
            region *= 1.0 - 0.16 * np.clip(
                sh[ps] * 1.6 - a[ps], 0.0, 1.0
            )  # soft shadow around the part
            region *= 1.0 - a[ps]
            region += g[ps] * a[ps]
        if cfg.blur_sigma_px > 0:
            frame = cv2.GaussianBlur(frame, (0, 0), cfg.blur_sigma_px)
        frame *= self._bands
        if (
            cfg.flicker_depth > 0
            and cfg.flicker_every > 0
            and frame_index % cfg.flicker_every in (0, 1, 2)
        ):
            frame *= 1.0 - cfg.flicker_depth * (1.0, 0.6, 0.25)[frame_index % cfg.flicker_every]
        frame += self._noise[frame_index % len(self._noise)]
        return np.clip(frame, 0, 255).astype(np.uint8)


def random_parts(
    count: int,
    cfg: SceneConfig,
    seed: int = 0,
    spacing_s: float = 0.6,
    kinds: tuple[str, ...] = PART_KINDS,
) -> list[PartSpec]:
    """A well-singulated stream of random parts (kind, lateral position, angle)."""
    rng = np.random.default_rng(seed)
    belt_w_in = (cfg.belt_bottom_px - cfg.belt_top_px) / cfg.px_per_in
    parts, t = [], 0.3
    for _ in range(count):
        kind = str(rng.choice(kinds))
        angle = float(rng.uniform(0, 360)) if kind.startswith("bolt") else float(rng.uniform(0, 60))
        parts.append(PartSpec(kind, t, float(rng.uniform(0.55, belt_w_in - 0.55)), angle))
        t += spacing_s * float(rng.uniform(0.9, 1.3))
    return parts


@dataclass
class _Timeline:
    t0_wall: float | None = None
    frame: int = 0
    stats: SourceStats = field(default_factory=SourceStats)


class SyntheticSource(FrameSource):
    """Frames from SyntheticRenderer. duration_s=None runs forever, repeating the part list."""

    def __init__(
        self,
        cfg: SceneConfig | None = None,
        parts: list[PartSpec] | None = None,
        realtime: bool = True,
        duration_s: float | None = None,
        start_ns: int = 1_000_000_000,
    ) -> None:
        self.cfg = cfg or SceneConfig()
        self.parts = parts if parts is not None else random_parts(40, self.cfg)
        self.renderer = SyntheticRenderer(self.cfg, self.parts)
        self._realtime = realtime
        self._duration_s = duration_s
        self._start_ns = start_ns
        self._cycle_s = (
            (max((p.t_enter_s for p in self.parts), default=0.0) + 2.0) if self.parts else 1.0
        )
        self._tl = _Timeline()
        self.info = SourceInfo(
            name="synthetic",
            width=self.cfg.width,
            height=self.cfg.height,
            fps=self.cfg.fps,
            live=False,
            details={"px_per_in": self.cfg.px_per_in, "realtime": realtime},
        )

    def start(self) -> None:
        self._tl = _Timeline()

    def read(self, timeout: float = 1.0) -> Frame | None:
        tl = self._tl
        t_s = tl.frame / self.cfg.fps
        if self._duration_s is not None and t_s >= self._duration_s:
            raise EndOfStream
        if self._realtime:
            if tl.t0_wall is None:
                tl.t0_wall = time.monotonic()
            delay = tl.t0_wall + t_s - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        scene_t = t_s if self._duration_s is not None else t_s % self._cycle_s
        image = self.renderer.render(scene_t, tl.frame)
        t_ns = self._start_ns + round(tl.frame * 1e9 / self.cfg.fps)
        frame = Frame(tl.frame, t_ns, t_ns, image)
        tl.frame += 1
        tl.stats.delivered += 1
        return frame

    def stop(self) -> None:
        pass

    def stats(self) -> SourceStats:
        return SourceStats(self._tl.stats.delivered, 0, 0)
