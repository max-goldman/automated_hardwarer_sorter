import glob, sys, json, cv2, numpy as np
from hwsorter import measure as M
from hwsorter.calibration import load_calibration
from hwsorter.config import load_config
from hwsorter.recording.store import RecordingReader
from hwsorter.vision.segment import Preprocessor, segment
from hwsorter.vision.features.holes import silhouette_crop, belt_level
name = sys.argv[1]
import os; cfg = load_config(os.environ.get("CFG")); rig = M._rig_from_calibration(load_calibration(), 1280, 800)
r = RecordingReader(sorted(glob.glob(f"/Users/maxalex357/Documents/automated_hardwarer_sorter/recordings/*{name}"))[-1])
pre = Preprocessor(rig.flat, rig.roi, cfg.segment)
recs = {json.loads(l)["i"]: json.loads(l) for l in open(sys.argv[2])} if len(sys.argv) > 2 else None
for i in range(0, len(r), int(os.environ.get("STEP", 2))):
    p = pre.apply(r.read(i))
    bl = [b for b in segment(p, cfg.segment, rig.mapper) if not b.touches_edge]
    if len(bl) != 1 or (recs is not None and i not in recs): continue
    b = bl[0]
    crop, mask, (x0, y0) = silhouette_crop(b, p.flat, 30)
    lvl = belt_level(crop, mask, 8, 19)
    m = cv2.moments(mask.astype(np.uint8), True); cx, cy = m["m10"]/m["m00"], m["m01"]/m["m00"]
    yy, xx = np.mgrid[:crop.shape[0], :crop.shape[1]]
    d = np.hypot(xx - cx, yy - cy)
    core = float(np.median(crop[d < 8])) / lvl
    mn = float(np.percentile(crop[d < 25], 5)) / lvl
    rc = recs[i] if recs else {"blur": None, "hole_kind": None, "verdict": "----"}
    print(i, None if rc["blur"] is None else round(rc["blur"], 1), "core %.3f p5 %.3f" % (core, mn), rc["hole_kind"], rc["verdict"][:4], "pos %.0f,%.0f" % (cx + x0, cy + y0), "lvl %.3f" % lvl)
