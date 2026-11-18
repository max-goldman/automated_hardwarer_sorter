"""Diagnostic: per-frame single-frame classification of every non-edge blob in a recording."""
import sys, glob, collections
import numpy as np
from hwsorter.config import load_config
from hwsorter import measure as M
from hwsorter.recording.store import RecordingReader
from hwsorter.contracts import Observation, Track, TrackFlag
from hwsorter.vision.features import extract
from hwsorter.vision.fusion import fuse
from hwsorter.vision.seam import SeamHandler
from hwsorter.vision.segment import Preprocessor, segment
from hwsorter.classify import RangeClassifier, load_library
from hwsorter.config import project_root
cfg = load_config()
cfg = cfg.model_copy(update={"features": cfg.features.model_copy(update={"min_valid_frames": 1})})
from hwsorter.calibration import load_calibration
rig = M._rig_from_calibration(load_calibration(), 1280, 800)
lib = load_library(project_root() / cfg.classify.library_path)
clf = RangeClassifier(lib, cfg.classify.model_copy(update={"min_frames": 1}))
for name in sys.argv[1:]:
    r = RecordingReader(sorted(glob.glob(f"recordings/*{name}"))[-1])
    pre = Preprocessor(rig.flat, rig.roi, cfg.segment); seam = SeamHandler(cfg.seam); seam.set_scale(rig.px_per_in)
    cnt = collections.Counter(); scores = collections.defaultdict(list); nblob = collections.Counter()
    for i in range(0, len(r), 2):
        f = r.read(i); p = pre.apply(f)
        blobs, _ = seam.process(p, segment(p, cfg.segment, rig.mapper))
        inner = [b for b in blobs if not b.touches_edge]
        nblob[len(inner)] += 1
        for b in inner:
            o = Observation(f.seq, f.t_capture_ns, b, rig.mapper.px_to_in(b.contour_px), True)
            v = extract(o, p, cfg.features)
            c = clf.classify(fuse(Track(1, [o], TrackFlag(0), finished=True), [v], cfg.features), TrackFlag(0))
            key = f"{c.verdict.value}:{c.part_class.value if c.part_class else '-'}"
            cnt[key] += 1; scores[key].append(c.score); 
            if c.verdict.value != "confident": cnt["worst:" + str(c.worst_feature)] += 1
    print(f"== {name}: {len(r)} frames; inner blobs/frame {dict(sorted(nblob.items()))}")
    for k, n in cnt.most_common(): print(f"   {k:32s} {n}" + (f"  median score {np.median(scores[k]):.2f}" if k in scores else ""))
