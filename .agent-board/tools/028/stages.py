"""Visualise segmentation stages for one frame: flat, grad, mask pre-trim, post-trim."""
import glob, sys, cv2, numpy as np
from hwsorter import measure as M
from hwsorter.calibration import load_calibration
from hwsorter.config import load_config
from hwsorter.recording.store import RecordingReader
from hwsorter.vision import segment as S
name, idx, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
import os; cfg = load_config(os.environ.get("CFG")); rig = M._rig_from_calibration(load_calibration(), 1280, 800)
r = RecordingReader(sorted(glob.glob(f"/Users/maxalex357/Documents/automated_hardwarer_sorter/recordings/*{name}"))[-1])
pre = S.Preprocessor(rig.flat, rig.roi, cfg.segment)
for i in range(0, idx + 1, 2): p = pre.apply(r.read(i))
cap = {}
orig = S._trim_soft
def spy(mask, sm, grad, valid, c):
    o = orig(mask, sm, grad, valid, c); cap.update(pre=mask.copy(), post=o.copy(), sm=sm, grad=grad); return o
S._trim_soft = spy
origw = S._segment_window
wins = []
def spyw(flat, win, *a):
    out = origw(flat, win, *a)
    if out and (not wins or max(b.area_px for b in out) > wins[0][1]): wins[:] = [(win, max(b.area_px for b in out), dict(cap))]
    return out
S._segment_window = spyw
blobs = S.segment(p, cfg.segment, rig.mapper)
cap = wins[0][2]
b = max(blobs, key=lambda b: b.area_px)
x0,y0,x1,y1 = b.mask_bbox_px
# window used: find from coarse crops
w = wins[0][0]
sm, grad = cap["sm"], cap["grad"]
def u8(a, lo, hi): return cv2.cvtColor(np.clip((a-lo)/(hi-lo)*255,0,255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
a = u8(sm, 0.85, 1.1); g = u8(grad, 0, 0.08)
pm = u8(cap["pre"].astype(float), 0, 1); po = u8(cap["post"].astype(float), 0, 1)
c = (b.contour_px - [w[0], w[1]])
for im in (a,):
    cv2.polylines(im, [np.round(c*4).astype(np.int32)], True, (0,0,255), 1, shift=2)
top = np.hstack([a, g]); bot = np.hstack([pm, po])
cv2.imwrite(out, np.vstack([top, bot]))
print("win", w, "bbox", b.mask_bbox_px)
if len(sys.argv) > 4:
    ya, yb, xa, xb = map(int, sys.argv[4].split(","))
    z = lambda im: cv2.resize(im[ya:yb, xa:xb], None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
    gg = u8(grad, 0, 0.04)
    pm = u8(cap["pre"].astype(float), 0, 1); po = u8(cap["post"].astype(float), 0, 1)
    soft = u8(((cv2.dilate(grad, cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5))) < cfg.segment.shadow_soft_gradient) & (np.abs(sm-1) < cfg.segment.shadow_trim_depth)).astype(float), 0, 1)
    a2 = u8(sm, 0.9, 1.05)
    cv2.imwrite(out.replace(".png", "_z.png"), np.vstack([np.hstack([z(a2), z(gg)]), np.hstack([z(pm), z(soft)])]))
