r"""detect_blackouts.py -- flags black/blank stretches within each Masters clip (someone's
phone pointed at the floor/ground, a pocket, or a genuinely dark no-visual moment in the
show) as metadata, rather than requiring a human to scrub through and notice by eye. Found
by hand on 07-The Day That Never Comes-C.MOV: the clip opens black/near-black and only
becomes a usable shot in its last couple seconds -- exactly the kind of thing worth knowing
about every clip before an editing decision, not just the one a human happened to flag.

Uses ffmpeg's own `blackdetect` filter (single decode pass, no re-encode, no GPU) -- much
cheaper than the person-detector sampling in detect_zoom_glow.py, since this is a simple
whole-frame luma threshold, not something that needs object detection.

Output per clip: "<clip-basename>-blackout.json" with each detected black interval (relative
to the clip's own local time) plus the fraction of the clip's total duration that's black.

Usage:
    py detect_blackouts.py <masters_dir> <metadata_out_dir> [--min-duration 0.5]
                                                              [--pic-threshold 0.98]
                                                              [--pix-threshold 0.10]
"""
import sys, os, re, json, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_song import find_clips, duration

BLACK_RE = re.compile(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)")


def detect_blackouts_in_clip(path, min_duration, pic_th, pix_th):
    vf = f"blackdetect=d={min_duration}:pic_th={pic_th}:pix_th={pix_th}"
    cmd = ["ffmpeg", "-v", "info", "-i", path, "-vf", vf, "-an", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    intervals = []
    for line in out.stderr.splitlines():
        m = BLACK_RE.search(line)
        if m:
            intervals.append({
                "start": float(m.group(1)),
                "end": float(m.group(2)),
                "duration": float(m.group(3)),
            })
    return intervals


def main():
    args = sys.argv[1:]
    masters, out_dir = args[0], args[1]
    min_duration = 0.5
    pic_th = 0.98
    pix_th = 0.10
    if "--min-duration" in args:
        min_duration = float(args[args.index("--min-duration") + 1])
    if "--pic-threshold" in args:
        pic_th = float(args[args.index("--pic-threshold") + 1])
    if "--pix-threshold" in args:
        pix_th = float(args[args.index("--pix-threshold") + 1])

    os.makedirs(out_dir, exist_ok=True)
    clips = find_clips(masters)
    log = []
    for i, path in enumerate(clips, 1):
        base = os.path.splitext(os.path.basename(path))[0]
        out_path = os.path.join(out_dir, base + "-blackout.json")
        print(f"[{i}/{len(clips)}] {os.path.basename(path)}", flush=True)
        try:
            dur = duration(path)
            intervals = detect_blackouts_in_clip(path, min_duration, pic_th, pix_th)
            total_black = sum(iv["duration"] for iv in intervals)
            result = {
                "clip": os.path.basename(path),
                "duration": dur,
                "params": {"min_duration": min_duration, "pic_th": pic_th, "pix_th": pix_th},
                "blackout_intervals": intervals,
                "total_black_seconds": total_black,
                "black_fraction": total_black / dur if dur else 0.0,
                # specifically flags a clip that OPENS or ENDS black -- the "pointed at the
                # floor/pocket for a while before/after the usable shot" pattern, as opposed
                # to a brief black cut in the middle
                "opens_black": bool(intervals and intervals[0]["start"] < 0.5),
                "ends_black": bool(intervals and intervals[-1]["end"] > dur - 0.5),
            }
            with open(out_path, "w") as fh:
                json.dump(result, fh, indent=2)
            log.append((os.path.basename(path), len(intervals), f"{result['black_fraction']*100:.0f}%", "OK"))
        except Exception as e:
            log.append((os.path.basename(path), 0, "0%", f"EXCEPTION: {e}"))
        print(f"  -> {log[-1]}", flush=True)

    print("\nSUMMARY")
    for row in log:
        print(row)


if __name__ == "__main__":
    main()
