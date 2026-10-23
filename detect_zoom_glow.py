r"""detect_zoom_glow.py -- flags transient "bad zoom" moments within a clip: the brief
overexposed/glowing window right after a wide-to-zoom transition, while the phone's exposure
is still catching up to the new framing -- NOT a static whole-clip quality score. Confirmed
against real footage: 16-Enter Sandman-K's person-box area jumps (wide->zoom) are followed a
few seconds later by elevated blown-highlight fraction inside that box; 15-One-H has a
textbook case at 1:14 (a performer blown out to a featureless white/yellow glow, no skin tone,
while the rest of the frame stays normally lit).

Method, per clip:
  1. Sample frames at a fixed interval (default 1s).
  2. Run a person detector (torchvision Faster R-CNN, COCO, GPU) on each frame; track the
     detected person's bounding-box area as a fraction of the frame.
  3. A "zoom transition" is a jump in that fraction between consecutive samples past
     --jump-threshold (default: area at least doubles, or grows by 8 percentage points,
     whichever is more lenient).
  4. After each transition, look at the following --settle-window seconds (default 6) for the
     peak fraction of the person's own box that's blown out (HSV V>245) -- this is the
     "glow" signal, scoped to the person, not the whole frame (a venue's own bright screen/
     lights elsewhere in frame is not the same failure).
  5. Flag a transition if that peak crosses --glow-threshold (default 5%).

Output: one JSON per clip, alongside a plain per-second trace, so a transition's surrounding
context is visible without re-running the detector.

Usage:
    py detect_zoom_glow.py <clip.MOV> <out.json> [--interval 1.0] [--jump-threshold 8]
                                                   [--settle-window 6] [--glow-threshold 5]
"""
import sys, os, subprocess, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_song import duration
import numpy as np
import cv2
import torch
from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_320_fpn, FasterRCNN_MobileNet_V3_Large_320_FPN_Weights

_model = None
_device = "cuda" if torch.cuda.is_available() else "cpu"


def get_model():
    global _model
    if _model is None:
        weights = FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.DEFAULT
        _model = fasterrcnn_mobilenet_v3_large_320_fpn(weights=weights).to(_device).eval()
    return _model


def grab_frame(path, t):
    cmd = ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1",
           "-f", "image2pipe", "-vcodec", "bmp", "-"]
    out = subprocess.run(cmd, capture_output=True)
    if not out.stdout:
        return None
    arr = np.frombuffer(out.stdout, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


@torch.no_grad()
def detect_person(img_bgr):
    model = get_model()
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    t = torch.from_numpy(img_rgb).permute(2, 0, 1).float().div(255.0).to(_device)
    out = model([t])[0]
    best = None
    for box, label, score in zip(out["boxes"], out["labels"], out["scores"]):
        if label.item() != 1 or score.item() < 0.5:
            continue
        if best is None or score.item() > best[1]:
            best = (box.cpu().numpy(), score.item())
    return best


# Fraction of the person box's own height (from its top) treated as "head region" for the
# glow check -- a real digitally-zoomed overexposure first shows on the head/face, and
# averaging the clipped-pixel fraction over the WHOLE person box (dark clothing included)
# dilutes an otherwise-obvious white-out into a modest-looking number. Confirmed against real
# footage: 15-One-H's confirmed-bad frame at 1:14 only read as 4-10% "clipped" with a
# whole-box average despite the performer's head/torso being a near-total white blob.
HEAD_FRAC_OF_BOX = 0.35


def trace_clip(path, interval):
    dur = duration(path)
    ts = np.arange(0.5, max(dur - 0.5, 1.0), interval)
    trace = []
    for t in ts:
        img = grab_frame(path, float(t))
        if img is None:
            continue
        h, w = img.shape[:2]
        det = detect_person(img)
        if det is None:
            trace.append({"t": float(t), "person": False})
            continue
        (x1, y1, x2, y2), score = det
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        box_frac = ((x2 - x1) * (y2 - y1)) / (w * h)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2].astype(np.float32)
        mask = np.zeros((h, w), dtype=bool)
        mask[y1:y2, x1:x2] = True
        clipped = float((v[mask] > 245).mean()) if mask.any() else 0.0
        head_y2 = y1 + int((y2 - y1) * HEAD_FRAC_OF_BOX)
        head_mask = np.zeros((h, w), dtype=bool)
        head_mask[y1:head_y2, x1:x2] = True
        head_clipped = float((v[head_mask] > 245).mean()) if head_mask.any() else 0.0
        trace.append({"t": float(t), "person": True, "box_area_frac": float(box_frac),
                       "person_clipped_frac": clipped, "head_clipped_frac": head_clipped,
                       "score": float(score)})
    return trace


def find_transitions(trace, jump_threshold_pp, settle_window, glow_threshold, lookback_window=4.0):
    """A zoom transition is detected against a TRAILING BASELINE (the minimum box_area_frac
    seen in the last `lookback_window` seconds), not just the immediately preceding sample --
    a real phone zoom (pinch/slider) ramps smoothly over 1-3s rather than jump-cutting between
    adjacent samples, and a same-interval-only comparison misses the cumulative change
    entirely. Confirmed against 15-One-G: its real zoom ramps box_area from 1.9% to 11.3%
    over ~2.5s with no single adjacent-sample step crossing an 8pp/2x threshold on its own."""
    events = []
    with_person = [p for p in trace if p["person"]]
    seen_transition_starts = set()
    for i in range(1, len(with_person)):
        cur = with_person[i]
        window_start = cur["t"] - lookback_window
        baseline_candidates = [p["box_area_frac"] for p in with_person if window_start <= p["t"] < cur["t"]]
        if not baseline_candidates:
            continue
        baseline = min(baseline_candidates)
        cur_area = cur["box_area_frac"]
        jumped = (cur_area - baseline) * 100 >= jump_threshold_pp or (baseline > 0 and cur_area / baseline >= 2.0)
        if not jumped:
            continue
        # dedupe: a smooth ramp re-triggers "jumped" on every sample along the way -- only
        # record the first sample of each distinct ramp (half the lookback window apart)
        if any(abs(cur["t"] - s) < lookback_window / 2 for s in seen_transition_starts):
            continue
        seen_transition_starts.add(cur["t"])
        window = [p for p in with_person if cur["t"] <= p["t"] <= cur["t"] + settle_window]
        peak_glow = max((p["head_clipped_frac"] for p in window), default=0.0)
        events.append({
            "transition_t": cur["t"],
            "pre_box_area_frac": baseline,
            "post_box_area_frac": cur_area,
            "peak_head_glow_frac_within_settle_window": peak_glow,
            "flagged": peak_glow * 100 >= glow_threshold,
        })
    return events


def main():
    args = sys.argv[1:]
    clip_path, out_path = args[0], args[1]
    interval = 1.0
    jump_threshold = 8.0
    settle_window = 6.0
    glow_threshold = 5.0
    if "--interval" in args:
        interval = float(args[args.index("--interval") + 1])
    if "--jump-threshold" in args:
        jump_threshold = float(args[args.index("--jump-threshold") + 1])
    if "--settle-window" in args:
        settle_window = float(args[args.index("--settle-window") + 1])
    if "--glow-threshold" in args:
        glow_threshold = float(args[args.index("--glow-threshold") + 1])

    print(f"Tracing {clip_path} ...", flush=True)
    trace = trace_clip(clip_path, interval)
    events = find_transitions(trace, jump_threshold, settle_window, glow_threshold)
    result = {
        "clip": os.path.basename(clip_path),
        "params": {"interval": interval, "jump_threshold_pp": jump_threshold,
                   "settle_window_s": settle_window, "glow_threshold_pct": glow_threshold},
        "trace": trace,
        "zoom_transitions": events,
        "any_flagged": any(e["flagged"] for e in events),
    }
    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=2)
    print(f"{len(events)} zoom transitions found, {sum(e['flagged'] for e in events)} flagged.")
    for e in events:
        marker = " <-- FLAGGED" if e["flagged"] else ""
        print(f"  t={e['transition_t']:.1f}  {e['pre_box_area_frac']*100:.1f}% -> "
              f"{e['post_box_area_frac']*100:.1f}%  peak_head_glow={e['peak_head_glow_frac_within_settle_window']*100:.1f}%{marker}")
    print("Saved:", out_path)


if __name__ == "__main__":
    main()
