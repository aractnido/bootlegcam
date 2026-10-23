r"""build_fullshow_video.py -- concatenates every per-song video from build_song.py --all
into one continuous full-show video, with a brief lower-left title card fading in at the
start of each song (DVD-chapter style): same transparent dark panel + plain white text
treatment as the audio covers (black@0.45 panel, Segoe UI Bold, no stroke -- relies on the
panel for contrast rather than a border, after Arial Bold read as "PowerPoint" and Impact
as "meme font" during cover-art iteration).

Each song gets its own title-overlay pass first (so the fade timing is always relative to
that song's own start, not a cumulative offset into the full show), then all the titled
segments are concatenated with -c copy -- safe because every pass uses identical output
codec params, matching build_song.py's own.

Usage:
    py build_fullshow_video.py <videos_dir> <output.mp4> [--font "C:/Windows/Fonts/segoeuib.ttf"]

<videos_dir> should be a build_song.py --all output folder ("NN-Title.mp4" files). Any file
whose name doesn't start with digits (e.g. a prior run's own output sitting in the same
folder) is ignored, so this is safe to point at the same folder it writes into.
"""
import sys, os, glob, re, subprocess, tempfile
from datetime import datetime, timezone
import numpy as np

PLAIN_RE = re.compile(r"^(\d+)-(.+)\.mp4$", re.IGNORECASE)


def ffprobe_creation_time(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
                           "-of", "csv=p=0", path], capture_output=True, text=True)
    return out.stdout.strip()


def ffprobe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(out.stdout.strip())


def title_from_filename(name):
    return re.sub(r"^\d+-", "", os.path.splitext(name)[0])


def measure_text_size(font, fontsize, text):
    """Exact rendered pixel (width, height) of `text` at `fontsize` in `font`, found by
    rendering it to a canvas and taking the bounding box of non-background pixels via numpy
    -- avoids a hard dependency on a font-metrics library (e.g. PIL, not installed here),
    reusing tools already on PATH for this pipeline (ffmpeg + numpy, same combo as the audio
    cross-correlation sync in build_song.py). Used so the title panel and the divider line
    beneath it are sized to the ACTUAL text, not a guessed character-count heuristic."""
    W, H = 2400, 300
    tmp_txt = os.path.join(tempfile.gettempdir(), f"_measure_{os.getpid()}_{abs(hash(text))}.txt")
    with open(tmp_txt, "w", encoding="utf-8") as fh:
        fh.write(text)
    font_esc = font.replace(":", r"\:")
    txt_esc = tmp_txt.replace("\\", "/").replace(":", r"\:")
    vf = (f"color=c=black:s={W}x{H}:d=1,"
          f"drawtext=fontfile='{font_esc}':textfile='{txt_esc}':fontcolor=white:fontsize={fontsize}:x=20:y=20")
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", vf, "-frames:v", "1",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    out = subprocess.run(cmd, capture_output=True)
    os.remove(tmp_txt)
    arr = np.frombuffer(out.stdout, dtype=np.uint8).reshape(H, W, 3)
    mask = arr.max(axis=2) > 40  # brighter than the near-black background
    if not mask.any():
        return (len(text) * fontsize * 0.55, fontsize * 1.2)  # fallback heuristic, shouldn't trigger
    ys, xs = np.where(mask)
    return (float(xs.max() - xs.min() + 1), float(ys.max() - ys.min() + 1))


def dominant_color(path, duration):
    """Average RGB sampled at 3 points within the middle third of the clip's duration --
    deliberately avoids the first/last third, where a crossfade can blend in the previous or
    next song's own color. Used as the title card's divider-line accent color, one per song."""
    t1, t2 = duration / 3.0, 2.0 * duration / 3.0
    samples = []
    for frac in (0.25, 0.5, 0.75):
        t = t1 + (t2 - t1) * frac
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1",
             "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True)
        if len(out.stdout) >= 3:
            samples.append(out.stdout[:3])
    if not samples:
        return (200, 30, 30)  # fallback red-ish, shouldn't trigger
    r = sum(s[0] for s in samples) // len(samples)
    g = sum(s[1] for s in samples) // len(samples)
    b = sum(s[2] for s in samples) // len(samples)
    return boost_accent(int(r), int(g), int(b))


def boost_accent(r, g, b):
    """Lifts a sampled color to a minimum saturation/brightness floor so the divider line
    reads as a visible accent even when the source footage itself is dark/desaturated (e.g.
    Blackened's muted brown-grey sampled almost exactly the panel's own near-black, making
    the line nearly invisible) -- same shadow-lift/saturation-boost idea already used on the
    FullShow cover ("make it bright, increase blacks, reduce bright until red is right,
    increase vibrance"), just applied here via HSV instead of by eye. Hue is preserved."""
    import colorsys
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    s = max(s, 0.45)
    v = max(v, 0.55)
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
    return (int(r2 * 255), int(g2 * 255), int(b2 * 255))


def main():
    videos_dir = sys.argv[1]
    out_path = sys.argv[2]
    font = "C:/Windows/Fonts/segoeuib.ttf"
    if "--font" in sys.argv:
        font = sys.argv[sys.argv.index("--font") + 1]
    font_esc = font.replace(":", r"\:")

    files = []
    for f in sorted(glob.glob(os.path.join(videos_dir, "*.mp4"))):
        base = os.path.basename(f)
        m = PLAIN_RE.match(base)
        if not m:
            continue
        files.append((int(m.group(1)), f, title_from_filename(base)))
    files.sort(key=lambda x: x[0])
    if not files:
        raise SystemExit(f"No 'NN-Title.mp4' files found in {videos_dir}")

    tmp_dir = os.path.join(videos_dir, "_fullshow_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    titled = []
    try:
        for num, path, title in files:
            out_tmp = os.path.join(tmp_dir, f"{num:02d}.mp4")
            # A "00-" file is this show's opener segment (e.g. a custom-titled intro built by
            # a show-specific script, like titlecard_intro.py) -- it already carries its own
            # title card, drawn with whatever text actually fits its purpose (which isn't
            # derivable from its filename the way a song title is). Re-running this script's
            # own filename-derived title pass on it would stack a second, wrong title card on
            # top. Just stream-copy it straight into the concat list instead -- same codec
            # params as every title pass here, so a copy concats cleanly with the rest.
            if num == 0:
                print(f"SKIP TITLING (already titled) {num:02d}-{title}:", flush=True)
                r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-c", "copy", out_tmp])
                if r.returncode != 0:
                    raise SystemExit(f"FAILED copying pre-titled {path}")
                titled.append(out_tmp)
                continue
            title_file = os.path.join(tmp_dir, f"{num:02d}_title.txt")
            with open(title_file, "w", encoding="utf-8") as fh:
                fh.write(title)
            title_file_esc = title_file.replace("\\", "/").replace(":", r"\:")

            # Title card: drawn on a trimmed+faded branch of the same video (so it inherits
            # the right frame size for free), then overlaid back onto the untouched main
            # branch. Panel size comes from measure_text_size() (the actual rendered text,
            # not a guess), with a thin accent divider underneath colored by this song's own
            # dominant_color() -- same "panel + divider" language as the audio covers, just
            # with the divider doing double duty as a per-song color accent instead of a
            # separator between title/subtitle (videos have no subtitle line).
            # drawbox uses iw/ih for frame size (its own native vars); drawtext uses
            # main_w/main_h instead -- mixing them up is the exact collision bug hit earlier
            # (drawbox's own "h" parameter shadows a bare "h" meaning frame height).
            fontsize = 96
            pad = 28
            divider_h = 8
            bottom_gap = 0.055  # fraction of frame height, gap from the very bottom edge
            text_w, text_h = measure_text_size(font, fontsize, title)
            box_w = text_w + 2 * pad
            box_h = text_h + 2 * pad
            dur = ffprobe_duration(path)
            cr, cg, cb = dominant_color(path, dur)
            color_hex = f"0x{cr:02x}{cg:02x}{cb:02x}"

            vf = (
                "split=2[main][basefortitle];"
                "[basefortitle]trim=0:5.3,setpts=PTS-STARTPTS,"
                f"drawbox=x=iw*0.025:y=ih-ih*{bottom_gap}-{box_h:.0f}-{divider_h}:"
                f"w={box_w:.0f}:h={box_h:.0f}:color=black@0.45:t=fill,"
                f"drawbox=x=iw*0.025:y=ih-ih*{bottom_gap}-{divider_h}:"
                f"w={box_w:.0f}:h={divider_h}:color={color_hex}@0.9:t=fill,"
                f"drawtext=fontfile='{font_esc}':textfile='{title_file_esc}':fontcolor=white:"
                f"fontsize={fontsize}:x=main_w*0.025+{pad}:"
                f"y=main_h-main_h*{bottom_gap}-{box_h:.0f}-{divider_h}+{pad},"
                "format=yuva420p,"
                "fade=t=in:st=0:d=0.4:alpha=1,"
                "fade=t=out:st=4.8:d=0.5:alpha=1"
                "[titlecard];"
                "[main][titlecard]overlay=0:0:enable='lte(t,5.3)'"
            )
            # This pass only draws a title card on the VIDEO -- audio is untouched, so stream-
            # copy it (never downgrade audio that doesn't need touching; see SKILL.md).
            # GPU encode (h264_nvenc), tuning pattern borrowed from the proven av1_nvenc
            # recipes in the concert-video-pipeline skill (VBR+CQ, bf/b_ref_mode/rc-lookahead/
            # g) -- h264 specifically, not av1, because this file still has to stream-copy-
            # concat with the other already-H.264 segments (including the pre-titled 00-Intro,
            # copied in untouched above) at the end of main(); mixing codecs there would break
            # that concat. Preset p5 / CQ 23, NOT that skill's own p7/CQ16-23 archival tuning:
            # those numbers chase every last bit of detail out of Nikon 4K HEVC masters at
            # ~190 Mbps -- iPhone source here never had that detail to begin with, so paying
            # p7's extra encode time for it is wasted (confirmed: p7 measured SLOWER
            # wall-clock than the libx264 preset it replaced, on identical phone-source
            # content -- see SKILL.md's "GPU encoding" section). CQ 23 is the concert-video-
            # pipeline's own "looks great, no visible loss" number on real footage, not a
            # guess. The filter chain above still runs on CPU either way, only the final
            # encode moves to the GPU.
            cmd = ["ffmpeg", "-y", "-v", "error", "-stats", "-hwaccel", "cuda", "-i", path,
                   "-vf", vf,
                   "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                   "-bf", "2", "-b_ref_mode", "2", "-rc-lookahead", "32", "-g", "240",
                   "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                   "-c:a", "copy", out_tmp]
            print(f"TITLING {num:02d}-{title}:", flush=True)
            r = subprocess.run(cmd)
            if r.returncode != 0:
                raise SystemExit(f"FAILED titling {path}")
            titled.append(out_tmp)

        list_path = os.path.join(tmp_dir, "_concat.txt")
        with open(list_path, "w", encoding="utf-8") as fh:
            for t in titled:
                fh.write(f"file '{t.replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'\n")

        first_ct = ffprobe_creation_time(files[0][1])
        cmd = ["ffmpeg", "-y", "-v", "error", "-stats", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy"]
        if first_ct:
            cmd += ["-metadata", f"creation_time={first_ct}"]
        cmd += [out_path]
        print("CONCATENATING full show...", flush=True)
        r = subprocess.run(cmd)
        if r.returncode != 0:
            raise SystemExit("FAILED final concat")

        if first_ct:
            dt = datetime.strptime(first_ct, "%Y-%m-%dT%H:%M:%S.%fZ")
            epoch = dt.replace(tzinfo=timezone.utc).timestamp()
            os.utime(out_path, (epoch, epoch))
        print("DONE:", out_path)
    finally:
        for t in titled:
            try:
                os.remove(t)
            except OSError:
                pass
        for f in glob.glob(os.path.join(tmp_dir, "*_title.txt")):
            try:
                os.remove(f)
            except OSError:
                pass
        try:
            os.remove(os.path.join(tmp_dir, "_concat.txt"))
        except OSError:
            pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


if __name__ == "__main__":
    main()
