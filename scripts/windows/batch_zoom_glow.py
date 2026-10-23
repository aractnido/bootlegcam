r"""batch_zoom_glow.py -- runs detect_zoom_glow.py's analysis over every clip in a Masters
folder in one process (loads the GPU model once, instead of once per clip). Writes one
"<clip-basename>-zoomglow.json" per clip into the given metadata output folder.

Usage:
    py batch_zoom_glow.py <masters_dir> <metadata_out_dir> [--interval 1.0]
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_song import find_clips
from detect_zoom_glow import trace_clip, find_transitions


def main():
    args = sys.argv[1:]
    masters, out_dir = args[0], args[1]
    interval = 1.0
    if "--interval" in args:
        interval = float(args[args.index("--interval") + 1])

    os.makedirs(out_dir, exist_ok=True)
    clips = find_clips(masters)
    log = []
    for i, path in enumerate(clips, 1):
        base = os.path.splitext(os.path.basename(path))[0]
        out_path = os.path.join(out_dir, base + "-zoomglow.json")
        print(f"[{i}/{len(clips)}] {os.path.basename(path)}", flush=True)
        try:
            trace = trace_clip(path, interval)
            events = find_transitions(trace, jump_threshold_pp=8.0, settle_window=6.0, glow_threshold=5.0)
            result = {
                "clip": os.path.basename(path),
                "params": {"interval": interval, "jump_threshold_pp": 8.0,
                           "settle_window_s": 6.0, "glow_threshold_pct": 5.0},
                "trace": trace,
                "zoom_transitions": events,
                "any_flagged": any(e["flagged"] for e in events),
            }
            with open(out_path, "w") as fh:
                json.dump(result, fh, indent=2)
            n_flagged = sum(e["flagged"] for e in events)
            log.append((os.path.basename(path), len(events), n_flagged, "OK"))
        except Exception as e:
            log.append((os.path.basename(path), 0, 0, f"EXCEPTION: {e}"))
        print(f"  -> {log[-1]}", flush=True)

    print("\nSUMMARY")
    for row in log:
        print(row)
    print(f"\nTotal clips: {len(clips)}, flagged clips: {sum(1 for r in log if r[2] > 0)}")


if __name__ == "__main__":
    main()
