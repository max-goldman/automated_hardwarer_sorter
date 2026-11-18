"""Per-blob dump: features, verdict, angle, centroid, speed (blur px), edge distance -> jsonl.

usage: dump.py <name> <out.jsonl> [--crops DIR] [--every N]
"""

import glob
import json
import math
import sys

import cv2
import numpy as np

import hwsorter
from hwsorter import measure as M
from hwsorter.calibration import load_calibration
from hwsorter.classify import RangeClassifier, load_library
from hwsorter.config import load_config, project_root
from hwsorter.contracts import Observation, Track, TrackFlag
from hwsorter.recording.store import RecordingReader
from hwsorter.vision.features import extract
from hwsorter.vision.fusion import fuse
from hwsorter.vision.seam import SeamHandler
from hwsorter.vision.segment import Preprocessor, segment

print("hwsorter:", hwsorter.__file__, file=sys.stderr)
name, out = sys.argv[1], sys.argv[2]
crops = sys.argv[sys.argv.index("--crops") + 1] if "--crops" in sys.argv else None
every = int(sys.argv[sys.argv.index("--every") + 1]) if "--every" in sys.argv else 10
import os; cfg = load_config(os.environ.get("CFG"))
cfg = cfg.model_copy(update={"features": cfg.features.model_copy(update={"min_valid_frames": 1})})
rig = M._rig_from_calibration(load_calibration(), 1280, 800)
lib = load_library(project_root() / cfg.classify.library_path)
clf = RangeClassifier(lib, cfg.classify.model_copy(update={"min_frames": 1}))
r = RecordingReader(
    sorted(glob.glob(f"/Users/maxalex357/Documents/automated_hardwarer_sorter/recordings/*{name}"))[-1]
)
pre = Preprocessor(rig.flat, rig.roi, cfg.segment)
seam = SeamHandler(cfg.seam)
seam.set_scale(rig.px_per_in)
prev = None
with open(out, "w") as fh:
    for i in range(0, len(r), 2):
        f = r.read(i)
        p = pre.apply(f)
        blobs, _ = seam.process(p, segment(p, cfg.segment, rig.mapper))
        inner = [b for b in blobs if not b.touches_edge]
        for b in inner:
            c = b.contour_px
            (cx, cy), (w, h), ang = cv2.minAreaRect(c.astype(np.float32))
            if w < h:
                ang += 90.0
            ang = ang % 180.0
            speed = math.nan
            if prev is not None and len(inner) == 1:
                dt = (f.t_capture_ns - prev[2]) / 1e9
                if dt > 0:
                    speed = math.hypot(cx - prev[0], cy - prev[1]) / dt
            o = Observation(f.seq, f.t_capture_ns, b, rig.mapper.px_to_in(c), True)
            v = extract(o, p, cfg.features)
            cl = clf.classify(
                fuse(Track(1, [o], TrackFlag(0), finished=True), [v], cfg.features), TrackFlag(0)
            )
            x0, y0, x1, y1 = b.mask_bbox_px
            rr = rig.roi
            edge = min(x0 - rr.left, rr.right - x1, y0 - rr.top, rr.bottom - y1)
            rec = dict(
                i=i,
                ang=ang,
                cx=cx,
                cy=cy,
                speed=speed,
                blur=speed * 0.008 if math.isfinite(speed) else None,
                edge=int(edge),
                verdict=cl.verdict.value,
                cls=cl.part_class.value if cl.part_class else None,
                score=cl.score,
                worst=cl.worst_feature,
                **{k: (None if not math.isfinite(x) else x) for k, x in v.values.items()},
            )
            fh.write(json.dumps(rec) + "\n")
            if crops and i % every == 0:
                pad = 30
                ys, xs = max(y0 - pad, 0), max(x0 - pad, 0)
                img = cv2.cvtColor(f.image[ys : y1 + pad, xs : x1 + pad], cv2.COLOR_GRAY2BGR)
                cv2.polylines(
                    img, [np.round((c - [xs, ys]) * 4).astype(np.int32)], True, (0, 0, 255), 1,
                    shift=2,
                )
                cv2.imwrite(f"{crops}/{name}_{i:04d}_{cl.verdict.value[:4]}.png", img)
        prev = (cx, cy, f.t_capture_ns) if len(inner) == 1 else None
