r"""pick_cover_frame.py -- picks a cover-art timestamp for a song's video by sampling frames
across the MIDDLE third of the video, averaging their color, then returning whichever sampled
frame's own color is closest to that average.

Rationale: a single fixed timestamp (e.g. the exact midpoint) can land on an outlier moment --
a lighting blackout, an empty stage -- in unpredictable live concert lighting. Picking the
sample closest to a wider window's own average color is a cheap way to land on something
visually typical of the song, without a human eyeballing a contact sheet every time.

Deliberately the middle third, not the first or last third: a song's own video (from
build_song.py) often includes a lead-in/tail overlap zone bleeding in content from the
previous or next song in the show (see "Cross-song content bleed" below) -- sampling near
either edge risks the cover actually coming from a different song.

Usage: py pick_cover_frame.py <video_path> [num_samples]
Prints the chosen timestamp (seconds, float) to stdout, nothing else.
"""
import sys
import subprocess


def sample_rgb(path, t):
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1",
         "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True)
    if len(out.stdout) < 3:
        return None
    return (out.stdout[0], out.stdout[1], out.stdout[2])


def main():
    path = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 15

    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True).stdout.strip())

    lo, hi = dur / 3.0, 2.0 * dur / 3.0
    times = [lo + (hi - lo) * i / (n - 1) for i in range(n)] if n > 1 else [(lo + hi) / 2.0]

    samples = []
    for t in times:
        rgb = sample_rgb(path, t)
        if rgb:
            samples.append((t, rgb))

    if not samples:
        print(dur * 0.5)
        return

    avg = tuple(sum(s[1][i] for s in samples) / len(samples) for i in range(3))
    best_t, best_dist = samples[0][0], float("inf")
    for t, rgb in samples:
        dist = sum((rgb[i] - avg[i]) ** 2 for i in range(3))
        if dist < best_dist:
            best_dist, best_t = dist, t

    print(best_t)


if __name__ == "__main__":
    main()
