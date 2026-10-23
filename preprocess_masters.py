r"""preprocess_masters.py -- standardizes each raw Masters clip INDEPENDENTLY, as its own
output file. No scene grouping, no base/cutaway logic, no audio sync -- deliberately separate
from build_song.py's assembly step, so this can be reviewed on its own before any editing
decision touches it.

Per clip:
  - HDR tone-mapped (same TM chain as build_song.py)
  - portrait clips: blurred-pillarbox to landscape (same technique as build_song.py)
  - landscape clips narrower than the target width: composited onto a blurred full-bleed
    background at a REDUCED fill fraction (default 75% of target width) instead of a flat
    upscale to 100% -- a straight 2x stretch (e.g. 1920->3840) looks soft; scaling to 75%
    (e.g. 1920->2880, a 1.5x upscale) and blurring in the remaining border on all four sides
    keeps more apparent sharpness. This is the "1K in 4K" treatment from the approved demo,
    generalized by fraction instead of a hardcoded absolute width.
  - landscape clips already >= the target width: plain scale, no compositing needed.

Output keeps the source's own basename (extension forced to .mp4) and its real creation_time
metadata + matching filesystem mtime, same discipline as build_song.py's own outputs.

Usage:
    py preprocess_masters.py <masters_dir> <output_dir> [--res WxH] [--fill-fraction 0.75]
"""
import sys, os, subprocess
from datetime import timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_song import find_clips, creation_time, duration, is_portrait, pick_audio_stream, TM, FPS

W_DEFAULT, H_DEFAULT = 3840, 2160
FILL_FRACTION_DEFAULT = 0.75


def get_dimensions(path):
    # ffprobe's csv writer appends a trailing comma even for a single requested field --
    # int("3840,") raises, so split rather than assume a bare number comes back.
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    w, h = out.stdout.strip().split(",")[:2]
    return int(w), int(h)


def build_filter(path, w, h, portrait, src_w, fill_fraction):
    if portrait:
        return (
            f"[0:v]{TM},split=2[a][b];"
            f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma=35[bg];"
            f"[b]scale=-2:{h}[m];"
            f"[bg][m]overlay=(W-w)/2:0,fps={FPS}[vout]"
        )
    if src_w < w:
        fg_w = int(w * fill_fraction) & ~1  # even width for yuv420p
        return (
            f"[0:v]{TM},split=2[a][b];"
            f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma=35[bg];"
            f"[b]scale={fg_w}:-2[m];"
            f"[bg][m]overlay=(W-w)/2:(H-h)/2,fps={FPS}[vout]"
        )
    return f"[0:v]{TM},scale={w}:{h},fps={FPS}[vout]"


def main():
    args = sys.argv[1:]
    masters, out_dir = args[0], args[1]
    w, h = W_DEFAULT, H_DEFAULT
    fill_fraction = FILL_FRACTION_DEFAULT
    if "--res" in args:
        i = args.index("--res")
        w, h = (int(x) for x in args[i + 1].lower().split("x"))
    if "--fill-fraction" in args:
        i = args.index("--fill-fraction")
        fill_fraction = float(args[i + 1])

    os.makedirs(out_dir, exist_ok=True)
    clips = find_clips(masters)
    log = []
    for path in clips:
        base = os.path.splitext(os.path.basename(path))[0]
        out_path = os.path.join(out_dir, base + ".mp4")
        try:
            ct = creation_time(path)
            src_w, src_h = get_dimensions(path)
            # is_portrait() reads rotation metadata -- true for raw phone footage (always
            # sensor-landscape-coded with a rotation flag) but blind to a clip that's already
            # been physically rotated (pixels correct, metadata intentionally cleared, as done
            # by hand for 07-The Day That Never Comes-C this session): such a clip has real
            # height > width with no rotation tag at all, so fall back to actual aspect ratio.
            portrait = is_portrait(path) or src_h > src_w
            vf = build_filter(path, w, h, portrait, src_w, fill_fraction)
            mode = "portrait-pillarbox" if portrait else ("1k-in-4k" if src_w < w else "plain-scale")
            # Audio is untouched by this pass (video treatment only) -- stream-copy it bit-for-
            # bit instead of decode+re-encode, so this review copy can never be a quality
            # downgrade from the source. pick_audio_stream() resolves the right stream
            # explicitly: a bare "0:a" is ambiguous once a clip has more than one audio stream
            # (iPhone "Spatial Audio" clips do -- see pick_audio_stream's docstring).
            audio_idx, _ = pick_audio_stream(path)
            # GPU encode (h264_nvenc), no CPU fallback -- same tuning as build_song.py's own
            # encode: preset p5 / CQ 23, not the concert-video-pipeline's own p7/archival
            # tuning (that's calibrated for Nikon 4K HEVC masters, not iPhone source -- see
            # build_song.py's own encode-settings comment and SKILL.md's "GPU encoding").
            cmd = ["ffmpeg", "-y", "-v", "error", "-stats", "-i", path,
                   "-filter_complex", vf,
                   "-map", "[vout]", "-map", f"0:{audio_idx}",
                   "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                   "-bf", "2", "-b_ref_mode", "2", "-rc-lookahead", "32", "-g", "240",
                   "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                   "-c:a", "copy",
                   "-metadata", f"creation_time={ct.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}",
                   out_path]
            print(f"PROCESSING ({mode}, src_w={src_w}): {os.path.basename(path)}", flush=True)
            r = subprocess.run(cmd)
            ok = r.returncode == 0
            if ok:
                epoch = ct.replace(tzinfo=timezone.utc).timestamp()
                os.utime(out_path, (epoch, epoch))
            log.append((os.path.basename(path), mode, "OK" if ok else "FFMPEG ERROR"))
        except Exception as e:
            log.append((os.path.basename(path), "?", f"EXCEPTION: {e}"))
        print(f"=== {log[-1][0]}: {log[-1][2]} ===\n", flush=True)

    print("\nSUMMARY")
    for row in log:
        print(row)


if __name__ == "__main__":
    main()
