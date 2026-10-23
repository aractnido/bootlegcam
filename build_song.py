r"""build_song.py -- general-purpose multicam song builder, reusable across concert folders.

Clips are grouped into real-time "scenes" using each clip's own recording timestamp
(QuickTime/MP4 `creation_time` metadata), NOT filename letter order and NOT raw duration.
A scene is a maximal run of clips whose [start, end) recording intervals actually
overlap in wall-clock time -- i.e. two (or more) phones genuinely pointed at the stage
during the same moment. Clips that don't overlap anything are their own one-clip scene.
Scenes are then joined in chronological order with a short crossfade.

Why this matters: a song's raw clips are very often NOT multiple angles of one
continuous shot -- just as often they're one person's recording broken into several
files by stopping/restarting the camera (no overlap at all), with real multi-angle
overlap only at a few specific handoff moments (if ever). An earlier version of this
script picked "the single longest clip in the whole song" as a base track and tried to
fit every other clip into it via audio cross-correlation alone; when clips didn't
actually overlap, a spurious correlation peak could still clear the confidence bar and
get spliced in as a fake "cutaway", producing a song where the audio is from the wrong
clip throughout (first caught on a 5-clip song where all 5 were one phone's non-
overlapping bursts, wrongly treated as a base+cutaway scene). Grouping by real overlap
first makes that failure mode structurally impossible -- see build_scenes().

Within a scene with real overlap, picking a video "base" by framing and picking an audio
"truth" track by actual signal quality are two SEPARATE decisions (see pick_truth_audio()) --
a loud, unclipped recording from a worse-framed phone can still be the right audio spine even
if a different, better-framed phone is what mostly plays on screen. The truth track's own
[start, end) becomes the scene's fixed real-world span: its own audio plays through
unmodified for that whole span (the song's audio clock), and the VIDEO is built to fit it
exactly, not the other way around. Within that span, every clip active at a given moment
(its own real recording interval, refined by audio cross-correlation the same way as always --
timestamps decide whether two clips overlap, correlation only refines exactly where, and is
distrusted if it disagrees with the timestamp estimate by more than a few seconds) is a video
candidate; exactly one is chosen per moment (ranked: landscape > steadiness > zoom/exposure
quality > longest), each trimmed to the REAL sub-span it's replacing -- never spliced in at
its own full raw length regardless of how long the real moment actually lasts. See
plan_scene_video() for the breakpoint construction. This replaced an earlier design (pick one
base clip, splice every overlapping clip in at full length) that could badly desync audio and
video once several long clips from different contributors clustered near the same moment --
see SKILL.md's "fixed: audio/video desync" section for the concrete evidence that motivated
the redesign.

A scene's clips aren't always all within the chosen truth track's own span: build_scenes()
groups by TRANSITIVE overlap (A-B-C can be one scene even if A and C never directly overlap),
so a clip can belong to the scene yet fall entirely outside whichever single clip quality
picked as truth. Rather than silently losing that footage, resolve_song_units() peels such
"orphan" clips off and re-groups them among themselves as additional chronological units in
the song -- same "never lose the only coverage of a moment" principle as the rest of this
pipeline (see SKILL.md's "uniqueness beats quality" section).

Naming convention, for files in a Masters folder: "NN-Title[-X].ext", where NN is the
song's position in the setlist (used for sort/output order) and X is an optional
chronological letter suffix (A, B, C...) when a song has multiple raw clips -- the
letter does not need to reflect real time order, only real timestamps do. A clip that
bridges two songs (e.g. a guitar-change/banter moment between song 03 and song 04) is
named "03-04-Title-X.ext" and is grouped into the *second* (destination) song, matching
how the bridge actually plays: trailing into the next song, not appended after the
previous one. This also avoids a subtler bug: a plain prefix match on "03-" would
otherwise also match "03-04-...", silently merging the bridge into song 03's clip list.

A third failure mode, distinct from both of the above: a clip can be correctly assigned
to its song and still contain the WRONG CONTENT at its tail, when whoever was filming
didn't stop recording at the song's actual end -- the raw file physically runs a few
seconds into the next song. (First caught when a 348s clip assigned to one song turned
out to share its last ~6 seconds, frame for frame, with the first clip of the next song:
one phone just kept rolling through the gap.) This can't be fixed by grouping logic --
the file itself needs trimming. See `apply_next_song_cutoff()`: `main()` passes each
song's build the real-world start time of the very next song's earliest clip, and any
clip whose recording interval runs past that point gets clamped to stop there.

Every segment is independently: trimmed, HDR tone-mapped (handles BT.2020/HLG ->
standard yuv420p/bt709 -- required, most players can't decode raw iPhone HDR),
pillarboxed if portrait (blurred-fill sides) or plain-scaled if landscape, and
frame-rate-normalized before any concat/crossfade (mixing phones = mismatched
timebases otherwise).

Requires: ffmpeg + ffprobe on PATH (with zscale/tonemap filter support), numpy.

Usage:
    py build_song.py <masters_dir> <song_prefix> <output.mp4> [--res 3840x2160]
    py build_song.py <masters_dir> --all <output_dir> [--res 3840x2160]   # every song found
"""
import sys, os, glob, re, json, subprocess
from datetime import datetime, timedelta, timezone
import numpy as np

W_DEFAULT, H_DEFAULT = 3840, 2160
FPS = 30
XFADE = 1.0  # seconds, for scene-to-scene joins
TM = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
      "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")

BRIDGE_RE = re.compile(r"^(\d{2})-(\d{2})-")
PLAIN_RE = re.compile(r"^(\d{2})-")
CLIP_GLOBS = ("*.MOV", "*.mov", "*.MP4", "*.mp4")


def find_clips(masters):
    """All raw clips in a Masters folder, deduped (Windows filesystems are
    case-insensitive, so "*.MOV" and "*.mov" would otherwise double-count every file --
    this bit an earlier QA script that scored every clip twice)."""
    seen = {}
    for pattern in CLIP_GLOBS:
        for f in glob.glob(os.path.join(masters, pattern)):
            seen[os.path.normcase(os.path.abspath(f))] = f
    return sorted(seen.values())


def ffprobe_json(path, extra):
    out = subprocess.run(["ffprobe", "-v", "error", "-of", "json"] + extra + [path],
                          capture_output=True, text=True)
    return json.loads(out.stdout or "{}")


def duration(path):
    j = ffprobe_json(path, ["-show_entries", "format=duration"])
    return float(j["format"]["duration"])


def creation_time(path):
    j = ffprobe_json(path, ["-show_entries", "format_tags=creation_time"])
    tags = j.get("format", {}).get("tags", {})
    ts = tags.get("creation_time")
    if not ts:
        raise ValueError(
            f"{path} has no creation_time metadata -- this pipeline groups clips by real "
            "recording timestamp and can't place this file. Re-exported/re-encoded clips "
            "often lose this tag; check the file came straight off the phone.")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    raise ValueError(f"{path} has an unrecognized creation_time format: {ts!r}")


def pick_audio_stream(path):
    """Absolute ffmpeg stream index of the best DECODABLE audio stream in a clip, plus its
    channel count.

    iPhones recording with "Spatial Audio" on write a second, immersive multi-channel audio
    stream alongside the normal stereo mix; ffprobe reports it as codec "unknown" since ffmpeg
    has no decoder for it. Letting ffmpeg pick a default has been observed to silently choose
    the undecodable stream on some clips (loudnorm/astats both return nothing, no error) while
    working fine on sibling clips from the same device -- not deterministic enough to trust.
    A bare "N:a" stream specifier in a filtergraph is similarly ambiguous once more than one
    audio stream exists. Always resolve explicitly instead, and prefer an actual stereo stream
    over a higher channel count we can't use without an unplanned (and silently lossy) downmix.
    """
    j = ffprobe_json(path, ["-show_entries", "stream=index,codec_type,codec_name,channels"])
    candidates = [
        (s["index"], s.get("codec_name"), s.get("channels"))
        for s in j.get("streams", [])
        if s.get("codec_type") == "audio" and s.get("codec_name") not in (None, "unknown")
    ]
    if not candidates:
        raise ValueError(f"{path} has no decodable audio stream.")
    stereo = [c for c in candidates if c[2] == 2]
    idx, codec_name, channels = (stereo[0] if stereo else candidates[0])
    return idx, channels


def is_portrait(path):
    j = ffprobe_json(path, ["-select_streams", "v:0",
                             "-show_entries", "stream_side_data=rotation:stream_tags=rotate"])
    stream = j.get("streams", [{}])[0]
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            return abs(sd["rotation"]) in (90, 270)
    rotate_tag = stream.get("tags", {}).get("rotate")
    if rotate_tag is not None:
        return abs(int(rotate_tag)) in (90, 270)
    return False


def load_wav(path, sr=16000):
    tmp = path + ".sync.wav"
    audio_idx, _ = pick_audio_stream(path)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-map", f"0:{audio_idx}",
                     "-ac", "1", "-ar", str(sr), tmp], check=True)
    import wave
    with wave.open(tmp, "rb") as w:
        n = w.getnframes()
        data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64)
        data -= data.mean()
    os.unlink(tmp)
    return data, sr


def best_offset(short, long_, sr):
    n = len(long_) + len(short) - 1
    nfft = 1 << (n - 1).bit_length()
    L = np.fft.rfft(long_, nfft)
    Sh = np.fft.rfft(short[::-1], nfft)
    corr = np.fft.irfft(L * Sh, nfft)
    valid = corr[len(short) - 1: len(long_)]
    best = int(np.argmax(valid))
    conf = valid[best] / (np.abs(valid).mean() + 1e-9)
    return best / sr, conf


def load_json_metadata(path, metadata_dir, suffix):
    """A clip's `<basename>-<suffix>.json` sidecar (analyze_audio_quality.py/detect_blackouts.py/
    detect_zoom_glow.py output), or None if `metadata_dir` wasn't given, the file doesn't exist
    (not every clip has been analyzed -- e.g. 00- b-roll is skipped by the audio-quality pass),
    or it fails to parse. Every caller treats None as "unknown" and degrades gracefully rather
    than crashing -- this pipeline must still work on a show that never ran any analysis pass."""
    if not metadata_dir:
        return None
    base = os.path.splitext(os.path.basename(path))[0]
    jf = os.path.join(metadata_dir, f"{base}-{suffix}.json")
    if not os.path.exists(jf):
        return None
    try:
        with open(jf) as fh:
            return json.load(fh)
    except Exception:
        return None


def load_clip_info(path, metadata_dir=None):
    d = duration(path)
    st = creation_time(path)
    audio_idx, audio_channels = pick_audio_stream(path)
    return {"path": path, "dur": d, "start": st, "end": st + timedelta(seconds=d),
            "portrait": is_portrait(path), "audio_idx": audio_idx, "audio_channels": audio_channels,
            "audio_quality": load_json_metadata(path, metadata_dir, "audioquality"),
            "blackout": load_json_metadata(path, metadata_dir, "blackout"),
            "zoomglow": load_json_metadata(path, metadata_dir, "zoomglow")}


def build_scenes(clip_infos):
    """clip_infos -> list of scenes (each a list of clip_info dicts), where a scene is
    a maximal run of clips with real wall-clock overlap. No tolerance padding: a clip
    that merely starts a second or two after the previous one ended (e.g. same phone,
    stopped and restarted) is NOT an overlap -- it's the next scene. This is the actual
    fix for the base/cutaway misclassification bug: duration/correlation heuristics
    never even get a chance to misfire on clips that don't overlap."""
    items = sorted(clip_infos, key=lambda c: c["start"])
    scenes = []
    cur = [items[0]]
    cur_end = items[0]["end"]
    for it in items[1:]:
        if it["start"] < cur_end:
            cur.append(it)
            cur_end = max(cur_end, it["end"])
        else:
            scenes.append(cur)
            cur = [it]
            cur_end = it["end"]
    scenes.append(cur)
    return scenes


def clip_overlap(c, lo, hi):
    """Real-world [start, end) overlap between `c` (any dict with "start"/"end" datetimes --
    a real clip_info, or one with a correlation-shifted "start"/"end" substituted in) and the
    window [lo, hi), or None if they don't overlap at all."""
    s, e = max(c["start"], lo), min(c["end"], hi)
    return (s, e) if e > s else None


def resolve_song_units(clip_infos):
    """build_scenes() groups clips by TRANSITIVE wall-clock overlap -- A can be grouped with C
    through a shared overlap with B even if A and C never directly overlap. But a scene's
    audio truth track (pick_truth_audio(), chosen by signal quality) doesn't necessarily SPAN
    the whole transitive group: a clip entirely outside the chosen truth's own [start, end)
    window would silently lose its footage if the whole scene were forced onto that window.
    Peels such "orphan" clips off and re-groups them among themselves instead (recursively --
    orphans can themselves overlap each other), so nothing is silently dropped. A clip that
    merely OVERLAPS truth's window but also extends before truth["start"] or past truth["end"]
    is a subtler version of the same problem: plan_scene_video() only ever looks inside truth's
    own [start, end) span, so that extending sliver would be silently dropped even though it's
    "covered" by the overlap test and never flagged as an orphan. Confirmed live: the real
    start of a song (a quiet intro before the rest of the band comes in) was only captured by a
    clip that started a few seconds before the clip picked as truth -- without this, the song's
    audio clock began AT truth's own start, silently cutting the intro. Fixed the same way as
    orphans: the earliest-starting lead-in (and symmetrically, the latest-ending tail-out, if
    any) is peeled off into its own small bracketing unit, trimmed to EXACTLY the portion
    outside truth's window -- never the clip's full length, which would duplicate the part
    already handled inside the main scene. If more than one clip extends into the same lead-in
    or tail-out zone, only the one with the most coverage there is used for now (a documented
    simplification, not a silent one -- seeing it happen in practice would justify a recursive
    fix later, same as the orphan case, but isn't worth the complexity until it actually does).

    Returns a list of chronologically-ordered "units": {"kind": "single", "clips": [c]} (no real
    overlap with anything -- plays start to finish), {"kind": "trim", "clip": c, "t0": ..,
    "t1": ..} (a clip-local sub-span bracketing a "multi" unit -- a lead-in or tail-out with no
    other footage competing for it), or {"kind": "multi", "clips": [...], "truth": c} (an
    overlapping group anchored to its chosen truth track's own span -- this now also covers a
    lead-in/tail-out zone that has its OWN extra coverage, see below).

    A zone clip's own single-coverage story isn't the whole picture either: a short clip that's
    entirely CONTAINED within the lead-in or tail-out zone (not just touching it) used to fall
    through the covered/orphan split as neither -- it doesn't overlap truth's own window
    (orphan test), but it's also never the single "most coverage" pick for VALUE its own zone
    (only that one clip becomes lead_unit/tail_unit). It then got peeled off as a plain "orphan"
    and resolved into its OWN separate chronological unit elsewhere in the song -- replaying, in
    full, real time the zone's own anchor clip already shows. Confirmed live: a scene's actual
    quality-ranked truth track was a short clip in the MIDDLE of a long continuous recording's
    span; every other short clip whose real time fell within that continuous recording's
    lead-in portion got silently orphaned and re-spliced in sequence AFTER the main span and its
    tail -- the song's own opening moment visibly replaying itself ~3.5 minutes in. Fixed by
    folding any orphan entirely contained in a chosen lead-in/tail-out zone INTO that zone,
    turning it from a single clip's flat "trim" into its own small "multi" (same truth-anchored,
    one-candidate-per-moment machinery as the main span, just anchored to the zone's own real
    span instead) -- so a genuine alternate angle during the zone can still be picked per-moment
    via the normal cutaway-preferred ranking, and nothing gets a second, out-of-place replay.
    No-op whenever a zone has no such contained orphans (the overwhelmingly common case) --
    produces the exact same lead_unit/tail_unit "trim" as before, clip-for-clip."""
    units = []
    for scene in build_scenes(clip_infos):
        if len(scene) == 1:
            units.append({"kind": "single", "clips": scene})
            continue
        truth = pick_truth_audio(scene)
        covered_paths = {c["path"] for c in scene if clip_overlap(c, truth["start"], truth["end"])}
        covered = [c for c in scene if c["path"] in covered_paths]
        orphans = [c for c in scene if c["path"] not in covered_paths]

        def zone_unit(lo, hi, anchor):
            """One lead-in/tail-out zone, spanning EXACTLY [lo, hi) -- the real-time gap between
            the scene's own full extent and truth's own window -- using every clip in the whole
            scene that overlaps this span AT ALL (not just ones fully CONTAINED in it), trimmed
            to it by the same plan_scene_video()/build_scene_segment() machinery as the main
            span. Replaces an earlier design that picked one "most coverage" clip as a fixed
            zone boundary and only folded in orphans fully contained inside THAT clip's own
            span: a clip that overlaps the zone but also extends past its edge (not contained,
            not inside truth's window either) fell through that design as an untracked orphan
            and got replayed whole, from scratch, elsewhere in the song -- confirmed on real
            footage on three songs (Unforgiven, Bell Tolls, The Day That Never Comes). Fixed by
            deriving [lo, hi) directly from the scene's own real extent (gap-free by
            build_scenes()'s construction) instead of from a single anchor clip's own span, so
            there's no "contained vs. merely overlapping" distinction left to miss -- any clip
            touching the zone at all is a candidate for it, same overlap test as `covered`'s own
            computation above."""
            dur = (hi - lo).total_seconds()
            if dur <= 0.1:
                return None
            zone_clips = [c for c in scene if clip_overlap(c, lo, hi)]
            if len(zone_clips) == 1:
                t0 = max((lo - anchor["start"]).total_seconds(), 0.0)
                t1 = min((hi - anchor["start"]).total_seconds(), anchor["dur"])
                return {"kind": "trim", "clip": anchor, "t0": t0, "t1": t1}
            zone_truth = {**anchor, "start": lo, "end": hi, "dur": dur,
                          "audio_real_start": anchor["start"]}
            return {"kind": "multi", "clips": zone_clips, "truth": zone_truth}

        lead_pool = [c for c in scene if c["start"] < truth["start"]]
        lead_unit = None
        if lead_pool:
            lead_anchor = min(lead_pool, key=lambda c: c["start"])
            lead_unit = zone_unit(lead_anchor["start"], truth["start"], lead_anchor)
            absorbed = {c["path"] for c in (lead_unit["clips"] if lead_unit["kind"] == "multi" else [lead_anchor])}
            orphans = [c for c in orphans if c["path"] not in absorbed]

        tail_pool = [c for c in scene if c["end"] > truth["end"]]
        tail_unit = None
        if tail_pool:
            tail_anchor = max(tail_pool, key=lambda c: c["end"])
            tail_unit = zone_unit(truth["end"], tail_anchor["end"], tail_anchor)
            absorbed = {c["path"] for c in (tail_unit["clips"] if tail_unit["kind"] == "multi" else [tail_anchor])}
            orphans = [c for c in orphans if c["path"] not in absorbed]

        if lead_unit:
            units.append(lead_unit)
        units.append({"kind": "multi", "clips": covered, "truth": truth})
        if tail_unit:
            units.append(tail_unit)
        if orphans:
            units.extend(resolve_song_units(orphans))

    def unit_start(u):
        return u["clip"]["start"] if u["kind"] == "trim" else min(c["start"] for c in u["clips"])
    units.sort(key=unit_start)
    return units


def apply_next_song_cutoff(clip_infos, cutoff, min_dur=0.2):
    """Clamp any clip whose recording interval runs past `cutoff` (the next song's
    earliest clip's real start time) -- its tail is actually next-song content, most
    often because someone's phone kept rolling through the gap between songs instead
    of being stopped. Returns a new list (clip dicts are copied, not mutated, so the
    caller's own references stay intact); a clip clamped down to near-nothing is
    dropped entirely rather than left as a degenerate near-zero-length segment."""
    if cutoff is None:
        return clip_infos
    out = []
    for c in clip_infos:
        if c["start"] < cutoff < c["end"]:
            new_dur = (cutoff - c["start"]).total_seconds()
            if new_dur <= min_dur:
                print(f"NOTE: dropping {c['path']} -- entirely past the next song's start")
                continue
            print(f"NOTE: trimming {c['path']} from {c['dur']:.1f}s to {new_dur:.1f}s -- "
                  "its tail runs past the next song's start")
            c = {**c, "dur": new_dur, "end": cutoff}
        out.append(c)
    return out


def build_segment_filter(path, t0, t1, label, w, h, portrait):
    """Returns (filter_string, out_label) for one trimmed, cleaned-up video segment.
    t1 is always an explicit end time (a clip's own -- possibly clamped -- duration),
    never open-ended: trimming to "the file's literal end" would silently bypass
    apply_next_song_cutoff() for any clip whose tail got clamped.

    Some phone clips are genuinely variable-frame-rate, with real gaps in frame delivery
    during static/low-motion stretches (confirmed: one clip's declared 59.94fps vs. its
    actual ~52.3fps average) -- trimming a short window of one can come up short, since
    `trim` stops at whatever frame PTS actually exist inside the window, and a real gap can
    be wide enough to end the window's available content well before its nominal length,
    with nothing to pad it back up (the `fps=` stage only resamples between known frames; it
    doesn't independently extend a stream that already ended short). Confirmed: a 2.359s trim
    of such a clip produced only 43 frames of a 4K build instead of the expected 71 -- the
    exact 28-frame (0.93s) video/audio mismatch first found building the show's 00-Intro
    segment. Fixed generically with a pad-then-recut: `tpad` can add (via cloning the last
    frame) up to the segment's own full nominal duration, generously covering any realistic
    shortfall, and the `trim=0:{dur}` right after cuts it back down to EXACTLY the requested
    duration regardless of how much padding was actually needed -- a no-op on an already
    full-length segment (the trim just discards the unneeded pad), a reliable guarantee of
    the right length on a short one. (`setpts=PTS-STARTPTS` not needed again after this trim
    since it starts at 0 already.)"""
    seg = f"trim={t0}:{t1}"
    dur = t1 - t0
    pad = f"tpad=stop_mode=clone:stop_duration={dur},trim=0:{dur}"
    if portrait:
        return (
            f"[{{IN}}:v]{seg},setpts=PTS-STARTPTS,{TM},split=2[{label}a][{label}b];"
            f"[{label}a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma=35[{label}bg];"
            f"[{label}b]scale=-2:{h}[{label}m];"
            f"[{label}bg][{label}m]overlay=(W-w)/2:0,fps={FPS},{pad}[{label}];"
        ), label
    else:
        return (
            f"[{{IN}}:v]{seg},setpts=PTS-STARTPTS,{TM},scale={w}:{h},fps={FPS},{pad}[{label}];"
        ), label


def pick_truth_audio(scene):
    """Chooses a scene's single continuous audio "truth" track by actual signal quality
    (analyze_audio_quality.py metadata), DECOUPLED from video base-selection (which cares
    about framing, not mic quality) -- these can legitimately be different clips. Tiered:
    a confirmed-clean (not clipped) recording always beats one we simply haven't analyzed,
    which in turn always beats one confirmed to be clipping. Within a tier, prefer the
    hotter (less negative) peak level -- more of the mic's dynamic range actually used, a
    reasonable proxy for signal-to-noise once clipping itself is ruled out -- then the
    longest real-world coverage (fewer scene-internal crossfade seams needed), then
    landscape framing only as a final, purely-for-determinism tiebreak."""
    def key(c):
        q = c["audio_quality"]
        if q is None:
            tier = 1
        else:
            tier = 0 if q.get("astats", {}).get("is_clipped") else 2
        peak = (q or {}).get("astats", {}).get("peak_level_db")
        peak = peak if peak is not None else -999.0
        return (tier, peak, c["dur"], not c["portrait"])
    return max(scene, key=key)


def compute_correlation_offsets(scene, truth):
    """Refines each non-truth clip's effective real-world start against the truth track's own
    audio via cross-correlation, for a tighter splice than the recording timestamp alone gives
    (phone clocks aren't frame-exact) -- same distrust rule as always: if correlation disagrees
    with the timestamp-predicted offset by more than 5s, the timestamp wins. Returns
    {path: timedelta} of the correction to ADD to a clip's own recorded start."""
    offsets = {truth["path"]: timedelta(0)}
    truth_audio, sr = load_wav(truth["path"])
    for c in scene:
        if c["path"] == truth["path"]:
            continue
        predicted = (c["start"] - truth["start"]).total_seconds()
        audio, _ = load_wav(c["path"], sr)
        # best_offset() assumes it's placing a SHORTER clip somewhere inside a LONGER one's
        # timeline -- not guaranteed now that truth is chosen by quality, not by duration. A
        # candidate longer than truth has no valid "fits inside" placement for that model and
        # would crash np.argmax on an empty slice; fall back to the timestamp estimate.
        if len(audio) > len(truth_audio):
            off = predicted
        else:
            off, conf = best_offset(audio, truth_audio, sr)
            if abs(off - predicted) > 5.0:
                off = predicted
        offsets[c["path"]] = timedelta(seconds=off - predicted)
    return offsets


def steadiness_score(c, shakiness_map):
    """Higher is steadier. None if this clip isn't in the (optional, best-effort)
    shakiness-report.csv -- callers must treat that as "unknown", not "shakiest"."""
    j = shakiness_map.get(os.path.basename(c["path"]))
    return -j if j is not None else None


def blackout_fraction(clip, t0, t1):
    """Fraction of a clip-LOCAL [t0, t1) window covered by a detected blackout/dead-footage
    interval (detect_blackouts.py -- phone pointed at the floor/a pocket). 0.0 if there's no
    blackout metadata for this clip (never penalizes a clip we haven't run that detector on)
    or the window is degenerate."""
    meta = clip.get("blackout")
    if not meta or t1 <= t0:
        return 0.0
    covered = 0.0
    for iv in meta.get("blackout_intervals", []):
        s, e = max(t0, iv["start"]), min(t1, iv["end"])
        if e > s:
            covered += e - s
    return covered / (t1 - t0)


def zoom_glow_penalty(clip):
    """Whole-clip count of flagged bad-zoom moments (detect_zoom_glow.py: a digital zoom that
    went soft/blown-out). Not yet localized to the specific sub-interval being ranked -- a
    deliberate simplification for this pass, see SKILL.md. 0 if unanalyzed (never penalizes a
    clip we haven't run that detector on)."""
    meta = clip.get("zoomglow")
    if not meta:
        return 0
    return sum(1 for t in meta.get("zoom_transitions", []) if t.get("flagged"))


def rank_candidate(c, shakiness_map):
    """Sort key for "which clip plays at this moment" -- higher wins. Order matches the
    documented ranking exactly: landscape > steadiness > zoom/exposure quality > longest."""
    s = steadiness_score(c, shakiness_map)
    return (not c["portrait"], s if s is not None else float("-inf"), -zoom_glow_penalty(c), c["dur"])


def clip_override(c, overrides):
    """A manual editorial override for clip `c`, keyed by its own basename (no extension) in
    `overrides`, or None. Lets a human curator correct/refine what the automated signals miss
    or can't express -- a false-negative zoom-glow detection on a specific stretch of a clip,
    or a deliberate directorial preference ("favor the steady drummer shot during this
    performer's feature moment") -- without touching the general-purpose ranking logic itself.
    Two independent keys, both clip-local-time windows, either or both may be present:
      "only": (t0, t1)  -- this clip is NEVER a candidate outside this window (hard exclude)
      "boost": (t0, t1) -- this clip outranks every other candidate while inside this window
                           (soft preference -- other clips still play everywhere else; this is
                           NOT the same as "only", which would make it the sole candidate)
    """
    if not overrides:
        return None
    return overrides.get(os.path.splitext(os.path.basename(c["path"]))[0])


def _in_window(window, local_a, local_b, pad=0.05):
    if not window:
        return False
    t0, t1 = window
    return local_a >= t0 - pad and local_b <= t1 + pad


def plan_scene_video(scene, truth, shakiness_map, offsets, overrides=None):
    """Partitions the truth track's own real-world [start, end) span into sub-intervals,
    choosing exactly ONE clip per sub-interval (never a serial playlist of every overlapping
    contributor), each trimmed to the REAL sub-span it covers -- never its own full raw
    length. This is what makes the resulting video's total duration equal truth["dur"]
    exactly, by construction, so it can never drift from the audio played underneath it.
    Returns [(clip, local_t0, local_t1), ...] in chronological order."""
    lo, hi = truth["start"], truth["end"]
    candidates = []
    for c in scene:
        eff_start = c["start"] + offsets.get(c["path"], timedelta(0))
        eff_end = eff_start + timedelta(seconds=c["dur"])
        ov = clip_overlap({"start": eff_start, "end": eff_end}, lo, hi)
        if ov:
            candidates.append((c, eff_start, ov[0], ov[1]))

    # Breakpoints come from every candidate's own overlap boundary, its blackout-interval
    # boundaries, AND any manual override window boundaries (all converted to real time) --
    # without these, a boundary inside an existing sub-interval can never actually take
    # effect: _in_window()/blackout_fraction() judge the WHOLE sub-interval at once, so a
    # blackout or an "only"/"boost" window that starts or ends partway through one would
    # either apply to the whole thing or not at all, instead of carving out its own precise
    # finer sub-interval.
    extra_points = set()
    for c, eff_start, s, e in candidates:
        for iv in (c.get("blackout") or {}).get("blackout_intervals", []):
            for t in (iv["start"], iv["end"]):
                p = eff_start + timedelta(seconds=t)
                if lo < p < hi:
                    extra_points.add(p)
        ov = clip_override(c, overrides) or {}
        for window in (ov.get("only"), ov.get("boost")):
            if window:
                for t in window:
                    p = eff_start + timedelta(seconds=t)
                    if lo < p < hi:
                        extra_points.add(p)
    points = sorted({lo, hi} | {p for _, _, s, e in candidates for p in (s, e)} | extra_points)
    raw_segments = []  # [a, b, clip, eff_start], mutable so adjacent folds can extend in place
    prev_clip = None
    for a, b in zip(points[:-1], points[1:]):
        if (b - a).total_seconds() <= 0:
            continue
        active = [(c, es) for c, es, s, e in candidates if s <= a and e >= b]
        if not active:
            continue  # shouldn't happen -- truth always covers its own [lo, hi) -- defensive only

        def local(c, es):
            return (a - es).total_seconds(), (b - es).total_seconds()

        # Manual "only" override: a candidate restricted to a specific clip-local window is
        # never eligible outside it -- unless that would eliminate every candidate, same
        # "never lose the only coverage of a moment" rule as the blackout filter below.
        restricted = [(c, es) for c, es in active
                      if (clip_override(c, overrides) or {}).get("only") is None
                      or _in_window(clip_override(c, overrides)["only"], *local(c, es))]
        active = restricted if restricted else active
        # Hard pre-filter: drop a candidate whose view of THIS moment is mostly blacked out,
        # unless that would eliminate every candidate -- never lose the only coverage of a
        # moment just because it's imperfect (see "uniqueness beats quality" in SKILL.md).
        usable = [(c, es) for c, es in active
                  if blackout_fraction(c, (a - es).total_seconds(), (b - es).total_seconds()) <= 0.5]
        pool = usable if usable else active
        # A real cutaway is ALWAYS preferred over the truth track's own video whenever one is
        # active and usable -- showing a genuinely different angle is the whole point of
        # multicam footage. Truth's video is only a fallback for moments nothing else covers.
        # landscape > steadiness > zoom/glow > longest only disambiguates BETWEEN cutaways,
        # UNLESS a manual "boost" override applies -- that outranks everything else, letting a
        # human curator override the automated ranking for a specific clip-local window.
        non_truth = [p for p in pool if p[0]["path"] != truth["path"]]
        choice_pool = non_truth if non_truth else pool

        def sort_key(pair):
            c, es = pair
            boosted = _in_window((clip_override(c, overrides) or {}).get("boost"), *local(c, es))
            return (boosted, rank_candidate(c, shakiness_map))

        best = max(choice_pool, key=sort_key)
        # Stickiness: if the clip already playing is still active here and ties the best
        # available candidate, keep it -- avoids a flicker-cut from two near-simultaneous
        # breakpoints (clock jitter) resolving to a "different" but equally-ranked clip.
        sticky = next((pair for pair in choice_pool if pair[0]["path"] == prev_clip), None) if prev_clip else None
        chosen = sticky if (sticky and sort_key(sticky) == sort_key(best)) else best
        if raw_segments and raw_segments[-1][2]["path"] == chosen[0]["path"]:
            raw_segments[-1][1] = b
        else:
            raw_segments.append([a, b, chosen[0], chosen[1]])
        prev_clip = chosen[0]["path"]

    # Fold any segment under MIN_SEG into its neighbor -- a clock-jitter-sized breakpoint
    # shouldn't become a visible micro-cut.
    MIN_SEG = 0.35
    i = 0
    while i < len(raw_segments) and len(raw_segments) > 1:
        a, b, clip, es = raw_segments[i]
        if (b - a).total_seconds() < MIN_SEG:
            if i == 0:
                raw_segments[1][0] = a
            else:
                raw_segments[i - 1][1] = b
            raw_segments.pop(i)
            continue
        i += 1

    out = []
    for a, b, clip, eff_start in raw_segments:
        t0 = max(0.0, (a - eff_start).total_seconds())
        t1 = min(clip["dur"], (b - eff_start).total_seconds())
        if t1 - t0 > 0.02:
            out.append((clip, t0, t1))
    return out


def load_shakiness_map(masters_dir):
    """Best-effort jitter/motion scores from a shakiness-report.csv, if one happens to exist
    next to the clips folder (or one level above it -- this show's lives beside the raw
    `Masters\\` dir, one level above `Masters\\Standarized\\`). Purely a bonus signal: missing,
    partial, or stale (referencing clips since renamed) is all fine -- steadiness_score()
    returns None for anything not in it, which rank_candidate() already treats as neutral."""
    here = masters_dir.rstrip("\\/")
    for csv_path in (os.path.join(here, "shakiness-report.csv"),
                      os.path.join(os.path.dirname(here), "shakiness-report.csv")):
        if os.path.exists(csv_path):
            try:
                import csv as csvmod
                out = {}
                with open(csv_path, newline="") as fh:
                    for row in csvmod.DictReader(fh):
                        out[row["file"]] = float(row["jitter_std_px"])
                return out
            except Exception:
                continue
    return {}


class LabelGen:
    def __init__(self):
        self.n = 0

    def __call__(self, tag):
        self.n += 1
        return f"{tag}{self.n}"


def build_scene_segment(unit, w, h, in_idx, filt, gen, shakiness_map, overrides=None):
    """Emits filter_complex lines for one resolve_song_units() unit; returns (video_label,
    audio_label, duration). A "single" unit just plays its one clip start-to-finish; a "trim"
    unit plays a specific clip-local [t0, t1) sub-span of one clip (a lead-in/tail-out
    bracketing a "multi" unit -- see resolve_song_units()) -- both use that one clip's own real
    audio for the span actually played, never a synthesized/silent track. A "multi" unit is
    anchored to its truth track's own [start, end) real-world span -- that track's own
    unmodified audio plays underneath for the whole span (the fixed audio clock), while
    plan_scene_video() fits exactly that many seconds of video from whichever clip is active at
    each moment, so video and audio duration match exactly by construction."""
    if unit["kind"] in ("single", "trim"):
        if unit["kind"] == "single":
            c, t0, t1 = unit["clips"][0], 0, unit["clips"][0]["dur"]
        else:
            c, t0, t1 = unit["clip"], unit["t0"], unit["t1"]
        lab = gen("sc")
        # t1 is always an explicit, possibly-clamped end, never None/open-ended -- "play to the
        # file's literal end" would silently undo apply_next_song_cutoff() (for "single") or
        # overrun into the main scene's own territory (for "trim").
        f, lab = build_segment_filter(c["path"], t0, t1, lab, w, h, c["portrait"])
        filt.append(f.format(IN=in_idx[c["path"]]))
        alab = gen("sa")
        filt.append(f"[{in_idx[c['path']]}:{c['audio_idx']}]atrim={t0}:{t1},asetpts=PTS-STARTPTS[{alab}];")
        return lab, alab, t1 - t0

    scene, truth = unit["clips"], unit["truth"]
    offsets = compute_correlation_offsets(scene, truth)
    plan = plan_scene_video(scene, truth, shakiness_map, offsets, overrides)

    labels = []
    for clip, t0, t1 in plan:
        lab = gen("sc")
        f, lab = build_segment_filter(clip["path"], round(t0, 3), round(t1, 3), lab, w, h, clip["portrait"])
        filt.append(f.format(IN=in_idx[clip["path"]]))
        labels.append(lab)

    raw = gen("scnraw")
    vlab = gen("scn")
    filt.append(f"{''.join('[' + l + ']' for l in labels)}concat=n={len(labels)}:v=1:a=0[{raw}];")
    # re-normalize fps right after concat -- concat's output timebase doesn't
    # necessarily match a plain segment's (mixing phones = mismatched source
    # timebases), and xfade refuses to run across a timebase mismatch.
    filt.append(f"[{raw}]fps={FPS}[{vlab}];")
    alab = gen("san")
    # Normally truth IS the real file, so its own local time 0 is truth["start"] by definition.
    # A synthetic zone truth (see resolve_song_units()'s zone_unit()) reuses a real clip's audio
    # as a lead-in/tail-out zone's spine but anchors "start"/"dur" to the ZONE's span, not the
    # clip's own -- "audio_real_start" (defaulting to truth["start"], a no-op for the normal
    # case) carries the clip's true own start so the trim lands at the right local offset.
    real_start = truth.get("audio_real_start", truth["start"])
    t0_audio = max(0.0, (truth["start"] - real_start).total_seconds())
    filt.append(f"[{in_idx[truth['path']]}:{truth['audio_idx']}]"
                f"atrim={t0_audio}:{t0_audio + truth['dur']},asetpts=PTS-STARTPTS[{alab}];")
    return vlab, alab, truth["dur"]


def build_song(clip_paths, out_path, w=W_DEFAULT, h=H_DEFAULT, next_song_start=None,
                metadata_dir=None, shakiness_map=None, overrides=None):
    shakiness_map = shakiness_map or {}
    clip_infos = [load_clip_info(p, metadata_dir) for p in clip_paths]
    clip_infos = apply_next_song_cutoff(clip_infos, next_song_start)
    song_start = min(c["start"] for c in clip_infos)
    units = resolve_song_units(clip_infos)

    inputs = [c["path"] for c in clip_infos]
    in_idx = {p: i for i, p in enumerate(inputs)}
    filt = []
    gen = LabelGen()

    scene_segs = [build_scene_segment(u, w, h, in_idx, filt, gen, shakiness_map, overrides) for u in units]

    vchain, achain, run_dur = scene_segs[0]
    for vlab, alab, dur in scene_segs[1:]:
        vout = gen("v")
        filt.append(f"[{vchain}][{vlab}]xfade=transition=fade:duration={XFADE}:offset={max(run_dur - XFADE, 0):.3f}[{vout}];")
        aout = gen("a")
        filt.append(f"[{achain}][{alab}]acrossfade=d={XFADE}[{aout}];")
        vchain, achain = vout, aout
        run_dur = run_dur + dur - XFADE

    filter_complex = "\n".join(filt)
    creation_time_str = song_start.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    cmd = ["ffmpeg", "-y", "-v", "error", "-stats"]
    for p in inputs:
        cmd += ["-i", p]
    # GPU encode (h264_nvenc), no CPU fallback -- tuning pattern borrowed from the proven
    # av1_nvenc recipes in the concert-video-pipeline skill (VBR+CQ, bf/b_ref_mode/
    # rc-lookahead/g), h264 specifically (not av1) so this stays byte-compatible with the
    # title-card pass's own h264_nvenc output in build_fullshow_video.py for its stream-copy
    # concat. Preset p5 / CQ 23, NOT that skill's own p7/archival tuning -- that's calibrated
    # for Nikon 4K HEVC masters at ~190 Mbps; this pipeline's iPhone source never had that much
    # detail to begin with, so p7's extra encode time buys nothing visible here (measured
    # slower wall-clock than the libx264 preset it replaced on identical content -- see
    # SKILL.md's "GPU encoding" section). CQ 23 is the concert-video-pipeline's own measured
    # "looks great, no visible loss" number on real footage. Decode and the filter_complex
    # above (tonemap, blur, overlay, concat, xfade) still run on CPU -- only the final encode
    # moves to the GPU; a real move of the filter graph itself onto CUDA frames would need
    # every filter in TM/build_segment_filter to have a CUDA-native equivalent, not attempted
    # here.
    cmd += ["-filter_complex", filter_complex,
            "-map", f"[{vchain}]", "-map", f"[{achain}]",
            "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0",
            "-bf", "2", "-b_ref_mode", "2", "-rc-lookahead", "32", "-g", "240",
            "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-c:a", "aac", "-b:a", "320k",
            "-metadata", f"creation_time={creation_time_str}", out_path]
    print("RUNNING:", out_path)
    print(filter_complex)
    r = subprocess.run(cmd)
    ok = r.returncode == 0
    if ok:
        # Mirror the real recording start into the filesystem mtime too, so the output
        # file sorts/displays by when the song actually happened, not when it was
        # rendered -- creation_time alone is invisible in Explorer/most file browsers.
        epoch = song_start.replace(tzinfo=timezone.utc).timestamp()
        os.utime(out_path, (epoch, epoch))
    return ok, filter_complex


def group_master_files(masters):
    """Scans a Masters folder and groups clips by song prefix. A clip named
    "AA-BB-Title-X.ext" (bridge convention) is grouped under BB, not AA -- see
    module docstring. Returns (groups: {prefix: [paths]}, title_source: {prefix: filename})."""
    files = find_clips(masters)
    groups = {}
    title_source = {}
    for f in files:
        base = os.path.basename(f)
        bm = BRIDGE_RE.match(base)
        if bm:
            groups.setdefault(bm.group(2), []).append(f)
            continue
        m = PLAIN_RE.match(base)
        if not m:
            continue
        g = m.group(1)
        groups.setdefault(g, []).append(f)
        title_source.setdefault(g, base)
    return groups, title_source


def title_from_filename(name):
    title = re.sub(r"^\d\d-", "", os.path.splitext(name)[0])
    return re.sub(r"-[A-Z]$|-Intro.*$", "", title)


def _parse_res(args):
    """Pulls an optional "--res WxH" pair out of args in place; returns (w, h)."""
    w, h = W_DEFAULT, H_DEFAULT
    if "--res" in args:
        i = args.index("--res")
        res = args[i + 1]
        w, h = (int(x) for x in res.lower().split("x"))
        del args[i:i + 2]
    return w, h


def _parse_metadata_dir(args):
    """Pulls an optional "--metadata-dir PATH" pair out of args in place; returns the path
    (or None -- every quality-score lookup degrades gracefully without one)."""
    if "--metadata-dir" in args:
        i = args.index("--metadata-dir")
        md = args[i + 1]
        del args[i:i + 2]
        return md
    return None


def _next_song_start(groups, order, prefix):
    """The real-world start time of the very next song's earliest clip, or None if
    `prefix` is the last song (or the only one). Used to clamp a clip that runs past
    the real song boundary -- see apply_next_song_cutoff()."""
    idx = order.index(prefix)
    if idx + 1 >= len(order):
        return None
    next_clips = groups[order[idx + 1]]
    return min(creation_time(p) for p in next_clips)


def main():
    args = sys.argv[1:]
    w, h = _parse_res(args)
    metadata_dir = _parse_metadata_dir(args)
    masters = args[0]
    shakiness_map = load_shakiness_map(masters)
    groups, title_source = group_master_files(masters)
    order = sorted(groups)  # "01" < "02" < ... < "16" sorts correctly: same-width, zero-padded
    if args[1] == "--all":
        out_dir = args[2]
        os.makedirs(out_dir, exist_ok=True)
        log = []
        for prefix in order:
            clips = sorted(groups[prefix])
            title = title_from_filename(title_source[prefix])
            out = os.path.join(out_dir, f"{prefix}-{title}.mp4")
            cutoff = _next_song_start(groups, order, prefix)
            try:
                ok, _ = build_song(clips, out, w, h, next_song_start=cutoff,
                                    metadata_dir=metadata_dir, shakiness_map=shakiness_map)
                log.append((prefix, title, len(clips), "OK" if ok else "FFMPEG ERROR"))
            except Exception as e:
                log.append((prefix, title, len(clips), f"EXCEPTION: {e}"))
            print(f"=== {prefix} {title}: {log[-1][3]} ===\n")
        print("\n\nSUMMARY")
        for row in log:
            print(row)
    else:
        prefix = args[1]
        out = args[2]
        if prefix not in groups:
            raise SystemExit(f"No clips found for prefix {prefix} in {masters}")
        cutoff = _next_song_start(groups, order, prefix)
        build_song(sorted(groups[prefix]), out, w, h, next_song_start=cutoff,
                   metadata_dir=metadata_dir, shakiness_map=shakiness_map)


if __name__ == "__main__":
    main()
