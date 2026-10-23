r"""analyze_audio_quality.py -- per-clip audio quality metadata: integrated loudness (LUFS),
true peak, loudness range, and clipping. Read-only (single decode pass per clip, via ffmpeg's
own `loudnorm` analysis mode and `astats`), no re-encode, no GPU -- cheap enough to run
alongside the heavier video batches.

Lets a device/mic-quality comparison (e.g. "does iPhone Air really sound worse than iPhone 15
Pro") be answered from real per-clip numbers instead of one spot-checked example, and feeds
the later "which clip's audio is the scene's source of truth" decision -- that should be
picked by actual signal quality, not just by whichever clip happens to be the longest/most
landscape (today's video-side base-selection criteria, which this is deliberately decoupled
from).

Output: one JSON per clip, "<clip-basename>-audioquality.json", in the given metadata dir.

Usage:
    py analyze_audio_quality.py <masters_dir> <metadata_out_dir>
"""
import sys, os, re, json, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_song import find_clips, pick_audio_stream


def loudnorm_stats(path, audio_idx):
    cmd = ["ffmpeg", "-i", path, "-map", f"0:{audio_idx}", "-af", "loudnorm=print_format=json", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    # loudnorm prints its JSON summary as the last brace-delimited block on stderr
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", out.stderr, re.DOTALL)
    if not m:
        return None
    d = json.loads(m.group(0))
    return {
        "integrated_lufs": float(d["input_i"]),
        "true_peak_db": float(d["input_tp"]),
        "loudness_range": float(d["input_lra"]),
        "input_thresh": float(d["input_thresh"]),
    }


def astats_summary(path, audio_idx):
    # ffmpeg's astats filter has no "Number of clipped samples" field (confirmed by dumping
    # every field name it actually prints) -- on the normalized float scale a sample magnitude
    # past 1.0 IS clipping, so detect it directly from Max/Min level instead. Peak count is the
    # number of samples sitting at that extreme value, a rough severity proxy (not a true
    # clipped-sample count, since levels just under 1.0 aren't captured).
    cmd = ["ffmpeg", "-i", path, "-map", f"0:{audio_idx}", "-af", "astats", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    # astats prints one block per channel, then an "Overall" block combining both -- a plain
    # search() over the whole text matches channel 1 first, silently reporting single-channel
    # numbers as if they were the clip's overall stats. Search only the text after "Overall".
    text = out.stderr
    overall_marker = text.rfind("Overall")
    text = text[overall_marker:] if overall_marker != -1 else text
    def grab(key):
        m = re.search(rf"{re.escape(key)}:\s*(-?[\d.]+)", text)
        return float(m.group(1)) if m else None
    max_level = grab("Max level")
    min_level = grab("Min level")
    is_clipped = (max_level is not None and max_level > 1.0) or (min_level is not None and min_level < -1.0)
    return {
        "peak_level_db": grab("Peak level dB"),
        "rms_level_db": grab("RMS level dB"),
        "flat_factor": grab("Flat factor"),
        "max_level": max_level,
        "min_level": min_level,
        "is_clipped": is_clipped,
        "peak_count": grab("Peak count"),
        "abs_peak_count": grab("Abs Peak count"),
    }


def main():
    masters, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    clips = [p for p in find_clips(masters) if not os.path.basename(p).startswith("00-")]
    log = []
    for i, path in enumerate(clips, 1):
        base = os.path.splitext(os.path.basename(path))[0]
        out_path = os.path.join(out_dir, base + "-audioquality.json")
        print(f"[{i}/{len(clips)}] {os.path.basename(path)}", flush=True)
        try:
            audio_idx, audio_channels = pick_audio_stream(path)
            ln = loudnorm_stats(path, audio_idx)
            st = astats_summary(path, audio_idx)
            result = {"clip": os.path.basename(path), "audio_channels": audio_channels,
                      "loudnorm": ln, "astats": st}
            with open(out_path, "w") as fh:
                json.dump(result, fh, indent=2)
            log.append((os.path.basename(path), "OK"))
        except Exception as e:
            log.append((os.path.basename(path), f"EXCEPTION: {e}"))
        print(f"  -> {log[-1]}", flush=True)

    print("\nSUMMARY")
    for row in log:
        print(row)


if __name__ == "__main__":
    main()
