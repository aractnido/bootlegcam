r"""make_show_cover.py -- builds a 16:9 cover/thumbnail image for the full-show video from a
single standout photo, with the show's title centered on it.

This is the full-show's own cover (for the video deliverable, e.g. a YouTube thumbnail), not a
per-song audio cover -- different visual language on purpose: centered title block instead of
the per-song covers' bottom-left panel (see Build-Audio.ps1's Make-Cover), because this image
stands alone rather than sitting next to 15 others in an album grid.

Before building this, ask the user (or check the show's raw photos) for one standout photo that
isn't just more of the same concert footage -- something that captures the show's identity: a
projected pre-show visual, a striking crowd shot, signage, merch, anything with its own
character. If nothing stands out, fall back to a wide, bright frame from the full-show video
itself (e.g. a crowd-wide shot), but a dedicated photo beats a video frame when one exists.

Never stretch a non-16:9 source to fit -- always CROP to 16:9 (centered by default) first, then
scale. Stretching visibly distorts faces/objects; cropping a few percent off top/bottom or
left/right never does.

Usage:
    py make_show_cover.py <source_image> <output.jpg> "<title>" "<subtitle>" [--brightness 0.08] [--saturation 1.1]

Example:
    py make_show_cover.py IMG_0571.jpg FullShow-Cover.jpg "METALLICA" "Live at The Sphere, Las Vegas - October 17, 2026"
"""
import sys
import os
import subprocess
import tempfile
import argparse


def probe_size(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
        capture_output=True, text=True)
    w, h = out.stdout.strip().split("x")
    return int(w), int(h)


def escape_for_filter(path):
    # ffmpeg filter option values use ':' as a key=value separator, so any literal ':' in a
    # path (a Windows drive letter) has to be escaped, and backslashes read more reliably as
    # forward slashes in filter strings -- same two-step fix used throughout this skill.
    p = path.replace("\\", "/")
    return p.replace(":", "\\:")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("title")
    ap.add_argument("subtitle", nargs="?", default="")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--brightness", type=float, default=0.08)
    ap.add_argument("--saturation", type=float, default=1.1)
    ap.add_argument("--font", default="C:/Windows/Fonts/segoeuib.ttf")
    args = ap.parse_args()

    sw, sh = probe_size(args.src)
    target_ratio = args.width / args.height
    src_ratio = sw / sh

    if src_ratio > target_ratio:
        # source is relatively wider than target -- crop the sides
        crop_w, crop_h = round(sh * target_ratio), sh
    else:
        # source is relatively taller than target -- crop top/bottom
        crop_w, crop_h = sw, round(sw / target_ratio)
    crop_x, crop_y = (sw - crop_w) // 2, (sh - crop_h) // 2

    tmp_dir = tempfile.mkdtemp(prefix="show_cover_")
    title_file = os.path.join(tmp_dir, "title.txt")
    with open(title_file, "w", encoding="utf-8") as fh:
        fh.write(args.title)
    font_esc = escape_for_filter(args.font)
    title_esc = escape_for_filter(title_file)

    vf = (
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={args.width}:{args.height},"
        f"eq=brightness={args.brightness}:saturation={args.saturation},"
        f"drawbox=x=0:y=ih*0.42:w=iw:h=ih*0.16:color=black@0.5:t=fill,"
        f"drawtext=fontfile='{font_esc}':textfile='{title_esc}':fontcolor=white:fontsize=130:"
        f"borderw=3:bordercolor=black@0.7:x=(w-text_w)/2:y=h*0.46-text_h/2"
    )
    if args.subtitle:
        sub_file = os.path.join(tmp_dir, "subtitle.txt")
        with open(sub_file, "w", encoding="utf-8") as fh:
            fh.write(args.subtitle)
        sub_esc = escape_for_filter(sub_file)
        vf += (
            f",drawtext=fontfile='{font_esc}':textfile='{sub_esc}':fontcolor=white:fontsize=44:"
            f"borderw=2:bordercolor=black@0.7:x=(w-text_w)/2:y=h*0.555-text_h/2"
        )

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", args.src, "-vf", vf, args.out]
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise SystemExit("FAILED building show cover")
    print("OK:", args.out)


if __name__ == "__main__":
    main()
