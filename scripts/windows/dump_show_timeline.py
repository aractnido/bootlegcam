r"""dump_show_timeline.py -- consolidates every Masters clip's real-world timing, device, and
orientation into one JSON, grouped by song, in chronological order. Not consumed by the
pipeline itself -- a human-readable/reviewable map of the whole show's real coverage (who
recorded what, when, overlapping how much), useful for spotting gaps, redundant coverage, and
device-attribution patterns at a glance instead of re-deriving it by hand each time.

Usage:
    py dump_show_timeline.py <masters_dir> <out.json>
"""
import sys, os, re, json, subprocess
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_song import find_clips, creation_time, duration, is_portrait, PLAIN_RE, BRIDGE_RE


def device_model(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=com.apple.quicktime.model",
         "-of", "default=nk=1:nw=1", path], capture_output=True, text=True)
    return out.stdout.strip() or None


def song_prefix_for(filename):
    bm = BRIDGE_RE.match(filename)
    if bm:
        return bm.group(2)
    m = PLAIN_RE.match(filename)
    return m.group(1) if m else None


def main():
    masters, out_path = sys.argv[1], sys.argv[2]
    clips = find_clips(masters)
    by_song = {}
    for path in clips:
        base = os.path.basename(path)
        skipped = base.startswith("SKIP-")
        lookup_name = base[len("SKIP-"):] if skipped else base
        prefix = song_prefix_for(lookup_name)
        if prefix is None:
            continue
        try:
            ct = creation_time(path)
            dur = duration(path)
        except Exception as e:
            by_song.setdefault(prefix, []).append({
                "file": base, "error": str(e)})
            continue
        entry = {
            "file": base,
            "start": ct.isoformat() + "Z",
            "duration": round(dur, 2),
            "end": (ct + timedelta(seconds=dur)).isoformat() + "Z",
            "device": device_model(path),
            "portrait": is_portrait(path),
            "skipped": skipped,
        }
        by_song.setdefault(prefix, []).append(entry)

    for prefix, entries in by_song.items():
        entries.sort(key=lambda e: e.get("start", ""))

    ordered = dict(sorted(by_song.items(), key=lambda kv: kv[0]))
    with open(out_path, "w") as fh:
        json.dump(ordered, fh, indent=2, default=str)
    total = sum(len(v) for v in ordered.values())
    print(f"Wrote {total} clips across {len(ordered)} songs -> {out_path}")


if __name__ == "__main__":
    main()
