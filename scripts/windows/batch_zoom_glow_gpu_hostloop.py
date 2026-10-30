r"""batch_zoom_glow_gpu_hostloop.py -- GPU-decode variant of batch_zoom_glow.py. Same analysis,
same "<clip-basename>-zoomglow.json" output schema, same sample times.

!! CONTAINS A PER-SAMPLE GPU -> HOST -> GPU ROUND TRIP. Read this before reusing it. !!

What runs where:
  * ffmpeg, entirely on the GPU: NVDEC decode (-hwaccel cuda -hwaccel_output_format cuda) and
    timestamp-only `select` sampling on CUDA frames. Frames that aren't sampled never leave GPU
    memory.
  * The ONLY per-frame host traffic: each sampled frame (one per --interval seconds of footage,
    default 1/s) is hwdownloaded, rotated and converted to BGR on the CPU, piped to Python, then
    uploaded again as a torch tensor for the person detector (fasterrcnn on CUDA). Its box comes
    back to the host for the HSV glow metrics (detect_zoom_glow.trace_clip, unchanged).
That host loop is inherent to the detector being a PyTorch model fed from an ffmpeg pipe. It is
bounded by the sample rate, NOT the video frame rate -- don't lower --interval much, and don't
copy this pattern into anything that touches every frame.

The original batch_zoom_glow.py instead spawns one CPU-decoding ffmpeg per sampled frame (a full
seek + decode each time). This variant decodes each clip once.

Usage:
    py batch_zoom_glow_gpu_hostloop.py <masters_dir> <metadata_out_dir> [--interval 1.0]
"""
import sys, os, json, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from build_song import find_clips
import detect_zoom_glow as dzg

# ffmpeg's own autorotate, done by hand: there's no transpose_cuda, so we decode with
# -noautorotate and rotate the (few) downloaded samples on the CPU instead
ROTATE_VF = {0: None, 90: "transpose=clock", -270: "transpose=clock",
             -90: "transpose=cclock", 270: "transpose=cclock", 180: "hflip,vflip", -180: "hflip,vflip"}


def probe_video(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,pix_fmt:stream_side_data=rotation", "-of", "json", path],
                         capture_output=True, text=True, check=True)
    s = json.loads(out.stdout)["streams"][0]
    rot = next((int(round(float(d["rotation"]))) for d in s.get("side_data_list", []) if "rotation" in d), 0)
    # ffprobe's "rotation" is av_display_rotation_get(); ffmpeg autorotates by its negation
    theta = -rot
    w, h = int(s["width"]), int(s["height"])
    if abs(theta) in (90, 270):
        w, h = h, w
    return w, h, theta, ("p010le" if "10" in s["pix_fmt"] else "nv12")


class NvdecSampler:
    """Streams sampled frames from one NVDEC pass; serves dzg.grab_frame()'s (path, t) calls,
    which trace_clip makes in increasing t order at exactly these sample times."""

    def __init__(self, path, ts, interval):
        self.w, self.h, theta, sw_fmt = probe_video(path)
        # first frame at/after each grid time == what grab_frame's accurate `-ss t` returns.
        # (an fps filter rounds to the nearest slot instead: verified ~half an interval late)
        vf = [f"select='gte(t\\,{float(ts[0])}+{interval}*selected_n)'",
              "hwdownload", f"format={sw_fmt}"]
        if ROTATE_VF[theta]:
            vf.append(ROTATE_VF[theta])
        cmd = ["ffmpeg", "-v", "error", "-noautorotate", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
               "-i", path, "-vf", ",".join(vf), "-fps_mode", "passthrough", "-frames:v", str(len(ts)),
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        self.ts = [float(t) for t in ts]
        self.i = 0

    def grab(self, path, t):
        n = self.w * self.h * 3
        while self.i < len(self.ts):
            buf = self.proc.stdout.read(n)
            this_t = self.ts[self.i]
            self.i += 1
            if len(buf) < n:
                return None
            if abs(this_t - t) < 1e-6:
                return np.frombuffer(buf, dtype=np.uint8).reshape(self.h, self.w, 3)
        return None

    def close(self):
        self.proc.stdout.close()
        self.proc.wait()


def trace_clip_gpu(path, interval):
    dur = dzg.duration(path)
    ts = np.arange(0.5, max(dur - 0.5, 1.0), interval)  # same sample grid as dzg.trace_clip
    sampler = NvdecSampler(path, ts, interval)
    original = dzg.grab_frame
    dzg.grab_frame = sampler.grab
    try:
        return dzg.trace_clip(path, interval)
    finally:
        dzg.grab_frame = original
        sampler.close()


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
            trace = trace_clip_gpu(path, interval)
            events = dzg.find_transitions(trace, jump_threshold_pp=8.0, settle_window=6.0, glow_threshold=5.0)
            result = {
                "clip": os.path.basename(path),
                "params": {"interval": interval, "jump_threshold_pp": 8.0,
                           "settle_window_s": 6.0, "glow_threshold_pct": 5.0, "decode": "nvdec"},
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
