---
name: bootlegcam
description: Turn raw phone footage of a concert -- shot handheld by multiple family members on their own iPhones/Android phones, in short overlapping or non-overlapping bursts per song, no shared timecode -- into one clean finished video per song plus an MLC-style audio publish (per-song FLAC + full-show FLAC with frame-grabbed covers) and an optional mastering pass (EQ, loudness, de-clipping) tuned for phone-in-a-crowd recordings. Use this whenever the user has a folder of raw phone clips from a concert they attended (not the band's own show -- see concert-video-pipeline for that) and wants them turned into shareable per-song videos, when multiple people's phones recorded the same moments and need syncing/choosing between angles, when footage is a mix of portrait and landscape phone clips, when iPhone HDR video won't play correctly, when the audio sounds boxy/boomy/clipped and needs cleanup, or when the user wants per-song/full-show audio extracted with cover art and full metadata tags from a multicam phone-footage folder.
---

# bootlegcam

Builds one clean video per song from a folder of handheld phone clips (iPhone or Android),
shot by one or more people with no shared timecode or clapperboard. Also publishes the
audio side (per-song FLAC + full-show FLAC, MLC-style covers) once the videos exist.

**Goal, explicitly: a "Contributions" folder of raw multicam phone clips in, four
deliverables out -- audio first, since the video muxes the mastered audio into itself, not
the other way around:**
1. **Per-song audio** -- mastered (EQ, loudness, de-clipping) lossless FLAC per song, embedded
   cover art; built from a continuous audio timeline whose source-of-truth clip per scene is
   chosen by actual signal quality, independent of which clip is chosen as the video base.
2. **Full-show audio** -- the per-song mastered FLACs re-encoded into one continuous
   full-concert lossless FLAC (never a stream-copy concat of finished FLACs -- see "Audio
   publish" below for why that silently truncates).
3. **Per-song 4K video** -- one clean multicam edit per song, video cutaways cut to fit the
   per-song mastered audio's clock, muxed with that audio re-encoded to AAC 320kbps (lossless
   audio in an MP4/MOV container has patchy device/player support; 320k AAC is the
   highest-quality-without-sacrificing-compatibility point, matching `concert-video-pipeline`'s
   own default).
4. **Full-show 4K video** -- all per-song videos concatenated into one `FullShow`, muxed with
   the full-show mastered audio at the same AAC 320kbps, with a bottom-left song-title overlay
   burned in per song.

## The core idea: group by real timestamp, not by filename or duration

Phone clips for the same song are very often **not** simultaneous multi-angle coverage --
just as often they're one person's recording broken into several files because they
stopped and restarted the camera app (a bathroom break, a song they didn't like, the
battery-saver screen lock). Real multi-angle overlap -- two people's phones pointed at the
stage during the exact same moment -- only happens at specific handoff points, if at all.

`build_song.py` groups clips into real-world "scenes" using each clip's own recording
timestamp (QuickTime/MP4 `creation_time` metadata) *before* doing anything else. Two clips
only become a base+cutaway candidate pair if their recording intervals genuinely overlap in
wall-clock time; everything else just plays in chronological order with a crossfade. This
matters because an earlier, cruder version of this idea -- pick the single longest clip as
a "base" and use audio cross-correlation alone to decide if every other clip belongs inside
it -- can misfire: a spurious correlation peak between two clips that never actually
overlapped can still clear the confidence bar, splicing one clip's video into another's
audio track for the whole song. Timestamps decide *whether* two clips are the same moment;
audio cross-correlation only refines *where exactly* to splice, once that's established.

## Folder convention

```
<show>/
  Masters/              raw clips, named "NN-Title[-X].MOV" (or .mp4)
  Videos/                <- build_song.py --all writes here
  Audio/                 <- Build-Audio.ps1 writes here
```

- `NN` = the song's position in the setlist (sets output order).
- `[-X]` = an optional letter suffix (A, B, C...) when a song has multiple raw clips. The
  letter is just a label for humans skimming the folder -- only the recording timestamp
  decides real order and overlap, so it's fine if the letters aren't chronological.
- A clip that bridges two songs (a guitar swap, between-song banter) is named
  `"03-04-Title-X.MOV"` and is grouped into the *second* (destination) song -- it trails
  into the next song, matching how it actually plays. Don't name it `"03-...-X.MOV"`; a
  plain prefix match would otherwise fold it into song 03 as well.
- A clip you don't want published (a tuning break, a false start) can be named with a
  `SKIP-` prefix -- `Build-Audio.ps1` excludes it from both the per-song output and the
  full show, same convention as the concert-video-pipeline skill.
- A clip whose recording runs past the start of the *next* song (someone kept their phone
  rolling through the song change instead of stopping it) gets automatically clamped at that
  boundary -- see "Cross-song content bleed" below. If the clamped-off tail turns out to be
  the only footage of the next song's real opening, it's worth manually extracting and
  archiving as its own `AA-BB-Title.ext` bridge clip (see that section).
- Rotation: iPhones always encode at the sensor's native landscape dimensions and store
  orientation as rotation metadata, not width/height -- a portrait clip is still
  3840x2160-coded. `build_song.py` reads this (rotation side-data, falling back to the
  `rotate` stream tag some Android phones use) automatically; you don't need to pre-rotate
  anything.

## Quick Start

The scripts live in `scripts\windows\`, next to this SKILL.md (a Windows-only pipeline today;
`scripts\macos\` is reserved for a future macOS port).

```powershell
cd <show-root>

# optional, in a separate window: guards step 1 against OOM on a clip-heavy show --
# see "RAM usage scales with clip count per song" below
scripts\windows\Watch-FfmpegMemory.ps1

# 1. Build every song found in Masters\ into Videos\NN-Title.mp4. --metadata-dir is optional
# (feeds the audio-quality/blackout/zoom-glow-informed scene assembly -- see "Fixed:
# audio/video desync" below) but strongly recommended once those analysis passes have run.
py scripts\windows\build_song.py Masters --all Videos --metadata-dir Masters-Standardized\metadata

# (or just one song, e.g. to re-run after fixing a clip)
py scripts\windows\build_song.py Masters 07 "Videos\07-Song Title.mp4" --metadata-dir Masters-Standardized\metadata

# 2. Publish audio once Videos\ has final per-song files
scripts\windows\Build-Audio.ps1 -Subtitle "Artist - Venue - Date"

# 3. Optional: master the published audio (EQ + loudness + de-click, see below)
scripts\windows\Master-Audio.ps1 -Artist "Band Name" -Album "Live at Venue" -Date "YYYY-MM-DD"

# 4. If step 3 ran, swap the mastered audio back into Videos\ (-c:v copy, no video re-encode)
scripts\windows\Mux-MasteredAudio.ps1 -Video Videos -Audio Audio

# 5. Optional: one continuous full-show video, title card per song
py scripts\windows\build_fullshow_video.py Videos "Videos\FullShow-Video.mp4"
```

`Production\Scripts\Run-V2-Pipeline.ps1` in this show's own folder chains all five steps for a
real end-to-end run -- a good reference for how they fit together on a show with its own
show-specific driver script.

Check the terminal SUMMARY from step 1 for any `FFMPEG ERROR` or `EXCEPTION` rows before
trusting the batch -- a failed song still leaves every other song's file intact (no
dependency between songs). Spot-check at least one multi-clip song by scrubbing through it
or pulling a few frames with `ffmpeg -ss <t> -i <file> -frames:v 1 out.png`.

## What each segment gets, regardless of scene type

- **HDR tone-mapped**: iPhones shoot BT.2020/HLG. Left unconverted, the encoder output is
  an unplayable "High 4:4:4 Predictive" H.264 profile almost nothing can decode. Every
  segment goes through a zscale/tonemap chain to standard yuv420p/bt709 first.
- **Pillarboxed if portrait**: a blurred, scaled copy of the same shot fills the left/right
  bars -- not black bars -- with the sharp original centered on top.
- **Frame-rate normalized** before any concat or crossfade: different phones (or different
  recording sessions on the same phone) can have different internal timebases, and
  `xfade`/concat silently refuse to run across a mismatch. Every segment -- and the output
  of every inner scene-concat -- gets an explicit `fps=` filter for this reason.

Output: 3840x2160 (override with `--res WxH`), yuv420p, bt709, libx264 crf20, AAC 320k --
plays everywhere. Pass `--metadata-dir <path>` to feed in `analyze_audio_quality.py`/
`detect_blackouts.py`/`detect_zoom_glow.py` output (see "Fixed: audio/video desync" below) --
optional, everything degrades gracefully without it.

## Output timestamps: real recording time, not render time

Every output file's `creation_time` metadata (and matching filesystem mtime) is stamped
with the song's real wall-clock start -- the earliest `creation_time` among the clips that
actually went into it -- not whenever the file happened to get rendered/mastered. Without
this, a batch render on one date (or a re-master weeks later) leaves every file dated with
that render day, even though the raw Masters clips all carry the true show date -- easy to
miss since neither Explorer's date column nor a quick `ffprobe` glance flags a *missing*
tag, only a wrong one, and by the time anyone notices, "what night was this?" isn't
answerable from the files themselves anymore. `build_song.py` sets this directly in its one
ffmpeg encode call; `Build-Audio.ps1` reads it back off each source video and reapplies it
to the FLAC it extracts (plus the FullShow concat, using the first song's start);
`Master-Audio.ps1` reads it from the FLAC before the mastering chain's SoX/ffmpeg WAV
round-trip (which carries no metadata at all) and reapplies it to the final encode, so a
mastering pass can't silently drop it either.

Each song's recorded range is conceptually `[start, end)` against real show time -- `end`
being `start + duration`, which `apply_next_song_cutoff()` already guarantees never runs
past the next song's own `start` (that's the whole point of the cutoff). There's often a
real gap between one song's `end` and the next song's `start` -- applause, stage banter --
that's correct, not a bug, whenever nobody's footage covers that gap.

If you ever need to backfill this on files rendered before this fix existed: recompute each
song's true start from its Masters clips, then
`ffmpeg -i in -map_metadata 0 -map 0 -c copy -metadata creation_time=<ISO8601> out` (lossless
remux, no re-encode) followed by `os.utime()`/`(Get-Item).LastWriteTimeUtc` to match.

## Cross-song content bleed

A clip can be correctly assigned to its song and still contain the WRONG content at its
tail: if whoever was filming didn't stop recording at the song's actual end, the raw file
runs a few seconds (sometimes much more) into the next song. Grouping logic can't catch this
-- the file itself needs trimming. `build_song.py` handles the general case automatically:
each song's build is given the real-world start time of the very next song's earliest clip,
and any clip whose recording interval runs past that point gets clamped there.

That automatic cap is a correct safety ceiling, not necessarily the precise boundary --
whoever starts filming the next song can react several seconds late, so the cap alone can
still leave real overrun content in the output. Two things that do NOT reliably find the true
boundary: comparing video frames near the suspected cutoff (a venue's background visual can
hold steady well across the real song change) and full-file audio cross-correlation (noisy,
two-different-phones concert audio gives inconsistent offsets depending on window size). What
does work: a fine-grained (~0.25s step) RMS loudness scan of just the clip's own tail --

```powershell
ffmpeg -ss <probe_start> -i clip.MOV -t <probe_len> `
  -af "aformat=sample_rates=16000:channel_layouts=mono,asetnsamples=n=4000:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-" `
  -f null -
```

-- looking for a loud -> quiet (the real gap/applause between songs) -> loud pattern, which
pinpoints the true cut point far more precisely than the automatic cap.

If the clamped-off tail is itself the only footage anyone has of the next song's true
opening (nobody else was rolling yet), don't just discard it: extract that window with
`ffmpeg -i clip.MOV -ss <start> -t <len> -c:v libx264 ... -metadata creation_time=<true UTC
start> out.mov` and feed it into the next song's build as an additional clip --
`build_scenes()` handles it with zero special-casing as long as the extracted clip's end
doesn't overlap the next song's earliest existing clip. Archive it permanently in `Masters\`
under the bridge-naming convention (`AA-BB-Title.ext`) rather than leaving it as a scratch
file -- this also makes a future automatic rerun converge on the same precise boundary on its
own, since the next song's earliest-clip-start calculation will include the archived clip's
earlier timestamp.

**Recipe: manually splitting one raw clip that spans two real content buckets.** Used
repeatedly this session (a "FanPhotoMontage" clip that turned out to open with real pre-show
b-roll but continue into a song's intro+performance; a "transition" bridge clip that turned
out to contain a real chunk of the PRECEDING song, not just banter; a song's own clip that
ran on past its outro banter into the NEXT song's opening) -- same core technique each time:
1. Pull a handful of frames spaced across the clip's duration to find roughly where the real
   content changes (don't guess from duration/filename alone).
2. Find the PRECISE boundary with the same RMS loudness scan from above, centered on the
   rough area -- look for a quiet dip (spoken banter is much lower RMS than either song's
   full-band mix) right before a sharp jump back up (the next song's music starting).
3. Split losslessly at that exact point: `-t <cut>`/`-ss <cut>` with `-c copy`, each piece
   getting its OWN correct `-metadata creation_time` (original start for piece 1, original
   start + cut-point-offset for piece 2) -- never just copy the source's original timestamp
   onto both pieces.
4. Rename/move each piece into whichever song bucket its content actually belongs to (new
   letter suffix, next available), archiving in `Masters\` same as any other clip -- never
   leave a split result as an unnamed scratch file.

Don't assume the concat demuxer can losslessly stitch two pieces back together if you also
need to physically rejoin them into one file (rare -- usually each piece just becomes its own
separate lettered clip and the pipeline's own chronological crossfade handles rejoining them
at build time, no manual splice needed): different clips from different phones can be
completely different codecs/bit-depth/frame-rate (confirmed: H.264 8-bit 24fps vs. HEVC
10-bit 59.94fps from two different iPhone models), and `-c copy` concat across that mismatch
fails silently -- no error, just a corrupt/bogus-duration output. A `filter_complex` concat
with explicit `fps=`/`format=` normalization on each input (full re-encode) is required if a
true single-file rejoin is ever actually needed.

**The mirror case also happens: a clip bleeding content from the PREVIOUS song at its head.**
Someone's phone can start recording near the end of one song and keep rolling through the real
gap into the next one -- same failure, opposite end. `apply_next_song_cutoff()` doesn't catch
this (it only clamps a song's own tail against the *next* song's start), and a generic
"clamp against the previous song's last known clip" safety net wouldn't either in the case
actually seen: the clip's own recording start was already *after* every cataloged clip of the
previous song had stopped, yet its content still clearly showed the previous song's visuals --
because nobody else happened to be filming during that stretch of real time, so there was no
clip interval to compare against and flag the overlap automatically. This one needs an actual
human watching the output and saying "this doesn't look right" (confirmed this way: user
reported "Enter Sandman starts around minute 3:25", and sure enough frame grabs at t=0 showed
the previous song's own iconic projected visuals, with the real song change landing within a
second of a `build_scenes()` scene boundary that was already there). Fix the same way as any
other misattribution: move the clip to where its content actually belongs and rebuild both
songs -- don't just delete it (see "keep as much as possible" below).

## Multi-angle quality curation: uniqueness beats quality

When a scene has more than one overlapping clip (real multi-angle coverage of the same
moment), two separate decisions are both in play and must not be conflated:

1. **Is this moment covered by anyone else at all?** If not, keep the footage regardless of
   its technical quality -- a shaky, blurry, badly-exposed clip of a moment nobody else
   recorded is still the only record of that moment. Never cut footage just for being
   low-quality without first checking whether another clip's `[start, end)` window already
   covers the same real time range.
2. **Only once a moment IS redundantly covered**, make a quality-based call about which
   clip(s) to actually use: `plan_scene_video()`'s per-moment ranking does this automatically
   (landscape > steadiness > zoom/exposure quality > longest -- see "Fixed: audio/video desync"
   below), and a moment that's mostly blacked out on one contributor's phone falls back to
   whichever other candidate is still active for it, never just dropped.

Concretely: before excluding any clip for quality reasons, list every OTHER surviving clip's
`[start, end)` window for that song and confirm the flagged clip's own window is a subset of
one (or the union of a few) of them. Only then is excluding it (via the `SKIP-` prefix
convention) actually "replacing it with something better" rather than "just losing that
moment."

**There's no longer a single scene-wide "base" clip.** An earlier design picked one base clip
per scene (preferring landscape) that played continuously under the whole thing, with
everything else spliced in as cutaways -- see "Fixed: audio/video desync" below for why that
was replaced with a per-moment choice (`plan_scene_video()`) fit to an independently-chosen
audio truth track (`pick_truth_audio()`), decoupled from framing entirely.

**Gotcha this surfaced** (still relevant to `compute_correlation_offsets()` today):
`best_offset()`'s cross-correlation assumes it's placing a *shorter* clip somewhere inside a
*longer* one's timeline -- not guaranteed once the reference track is chosen by audio quality
rather than by duration. A candidate longer than the truth track it's being checked against has
no valid "fits inside" placement for that model and would crash with `ValueError: attempt to
get argmax of an empty sequence`. Guarded in `compute_correlation_offsets()`: when a candidate's
own audio is longer than truth's, skip correlation entirely and fall straight back to the
timestamp-derived predicted offset.

**Detecting a bad digital zoom (blurry + overexposed "glow"): `detect_zoom_glow.py`.** A
digitally-zoomed phone shot in a dark venue often loses detail (soft/blurry, since it's just
upscaling, not a real optical zoom) and can blow out the subject if the phone's auto-exposure
wasn't re-locked onto the now much-smaller/brighter subject in frame (matrix-metering the
whole dark scene instead of spot-metering the subject -- the SLR equivalent of this same
failure). Key insight from direct user feedback: **this is a transient artifact at the moment
of the zoom, not a static whole-clip quality score** -- the few seconds right after a wide-to-
zoom transition, while the phone's exposure is still catching up, not the clip as a whole.

Method (`detect_zoom_glow.py`, GPU, `torchvision`'s `fasterrcnn_mobilenet_v3_large_320_fpn`,
pretrained COCO weights): sample frames at a fixed interval, track the detected person's
bounding-box area as a fraction of frame over time, and look at the blown-highlight fraction
(`V > 245` in HSV) specifically inside that box right after a detected zoom.

Two real bugs found and fixed by checking against ground truth the user pointed out by eye
(exact clip + timestamp, e.g. "15-One-G... zooms in second 18 or so... second 25 the head is
white"):
1. **Gradual zooms were missed entirely.** A real phone zoom (pinch/slider) ramps smoothly
   over 1-3 seconds; comparing only ADJACENT samples never sees a single step cross the jump
   threshold even though the cumulative change (confirmed: 1.9% -> 11.3% box area over ~2.5s)
   is obviously a real zoom. Fixed by comparing each sample against a trailing-window baseline
   (the minimum box-area seen in the last few seconds), which catches ramps, not just cuts.
2. **Whole-person-box averaging dilutes a real white-out into a small number.** A confirmed-bad
   frame (user: "the head is white") only read as ~5-10% "clipped" when averaged over the
   whole body (dark clothing drags the average down), even though the head/face was a
   featureless white blob. Fixed by scoring only the top ~35% of the person box (the head/
   upper-body region) for the glow fraction, not the whole box.
- **Caveat still true**: "subject brighter than background" is NOT a reliable signal at a venue
  with its own huge bright screen (the Sphere) -- the background can easily be brighter than
  the performers. The glow check is scoped to the person's own region for this reason.
- **Still unvalidated further**: colored stage lighting (blue/red gels) can desaturate a face
  for reasons unrelated to sensor clipping; a face-landmark-based "skin tone absent" check was
  tried (`keypointrcnn_resnet50_fpn`) and did NOT cleanly separate known-good from known-bad
  examples in a small sample -- the current head-region-blown-highlight approach is simpler and
  validated better, but hasn't been tested at scale yet.

Combine with a plain Laplacian-variance blur score (`cv2.Laplacian(...).var()`, downscaled
first so native-resolution differences between contributors' phones don't bias the comparison)
for the "soft/blurry zoom" half of the same complaint -- note a confirmed-good clip (per direct
user review) scored in the same blur range as a clip excluded for being blurry, so this score
alone isn't a reliable absolute threshold; it needs more than one clip's worth of calibration
before trusting it unsupervised.

`batch_zoom_glow.py <masters_dir> <metadata_out_dir>` runs this over every clip in one process
(loads the GPU model once), writing `<clip>-zoomglow.json` per clip.

**Blackout/dead-footage detection: `detect_blackouts.py`.** A clip can also just be pointed at
the floor/a pocket for a while before or after its usable portion -- found by a direct user
review ("07-The Day That Never Comes-C is bad, a contributor was pointing to the floor... but if
there's nothing else available, use it"). Unlike the zoom/glow check, this doesn't need object
detection -- ffmpeg's own `blackdetect` filter (single decode pass, CPU only) finds black/blank
intervals directly. Flags whether a clip opens or ends black specifically (the "pointed away
for a while" pattern), separate from a brief black cut in the middle. Run via the same
`<masters_dir> <metadata_out_dir>` batch pattern as above.

**Audio-quality metadata: `analyze_audio_quality.py`.** Per-clip integrated loudness (LUFS),
true peak, loudness range (`ffmpeg loudnorm`'s analysis pass) and peak/RMS level + clipping
(`astats`) -- both single-pass, read-only, no re-encode. Written to support two things: (1)
answering "does one contributor's phone actually have worse mics" with real numbers instead of
one spot-checked clip, and (2) the audio-source-of-truth selection described below.

Run at scale across all clips in `Masters\Standarized\` (the raw-originals archive, **not**
`Masters-Standardized\`'s re-encoded review copies -- see "Never silently downgrade audio"
below for why). Confirmed with real numbers: iPhone Air clips hit >=0 dBFS true peak (genuine
digital clipping) on 22 of 24 clips; zero clips from any other device model did, even though
integrated loudness is nearly identical across devices (~-12 to -13 LUFS). This is what backed
up a user report of "iPhone Air has the worst mics" with an actual signal-level cause (hot mic
gain driving past full scale) instead of just a loudness or noise-floor difference.

**Two real bugs found running this, both fixed:**
1. `astats` has no `"Number of clipped samples"` field at all (confirmed by dumping every field
   name the filter actually prints) -- the original regex could never match, on any clip. Real
   clipping is detected directly instead: `Max level > 1.0` or `Min level < -1.0` on the
   normalized float scale IS clipping (full-scale is exactly 1.0), with `Peak count` as a rough
   severity proxy (how many samples sit at that exact extreme -- not a true "how many samples
   clipped" count, since levels just under 1.0 aren't captured by it).
2. `astats` prints one block per channel, then an `Overall` block combining both -- a plain
   `re.search()` over the whole stderr text matches channel 1's block first, silently reporting
   single-channel numbers (`peak_level_db`, `rms_level_db`, `flat_factor`) as if they were the
   clip's combined stats. Fixed by searching only the text after the last `"Overall"` marker.

Output schema per clip (`<clip>-audioquality.json`): `audio_channels`, `loudnorm` (integrated
LUFS, true peak dB, loudness range, threshold), `astats` (peak/RMS level dB, flat factor, max/
min level, `is_clipped`, peak/abs-peak count).

## Pre-processing every clip independently: `Masters-Standardized\` + `preprocess_masters.py`

Before any editing/assembly decision, every raw clip can be standardized on its own --
HDR-tonemapped, pillarboxed/composited to the target canvas -- as an independent output file,
kept in a separate `Masters-Standardized\` folder so `Masters\` stays untouched originals.
This is deliberately decoupled from `build_song.py`'s assembly step: it lets a human review
each clip's standalone treatment before trusting it inside a multi-clip scene, and avoids
redoing this per-clip work inside every future scene-assembly experiment.

```powershell
py scripts\windows\preprocess_masters.py Masters Masters-Standardized [--res WxH] [--fill-fraction 0.75]
```

Per clip, by orientation/resolution:
- **Portrait**: same blurred-pillarbox-to-landscape treatment as `build_song.py`'s own.
- **Landscape, narrower than the target canvas ("1K-in-4K")**: composited onto a blurred
  full-bleed background at a REDUCED fill fraction (default 75% of target width) instead of a
  flat upscale to 100% -- a straight 2x stretch (e.g. 1920->3840) looks soft; scaling to 75%
  (e.g. 1920->2880, a 1.5x upscale) and blurring in the remaining border on all four sides
  keeps more apparent sharpness. This is the demoed-and-approved "1K-in-4K" treatment,
  generalized by fraction instead of a hardcoded absolute width.
- **Landscape, already >= target width**: plain scale, no compositing needed.

Output keeps the source's own basename (extension forced to `.mp4`) and real `creation_time`
+ matching mtime, same discipline as `build_song.py`'s own outputs. `00-` (FanPhotoMontage)
clips are included here even though that bucket isn't part of the published show (see
`run_v2.py`'s song order) -- worth standardizing anyway, since a "generic pre-show b-roll"
clip can turn out to contain real song content on closer inspection (confirmed this way: one
`00-FanPhotoMontage` clip turned out to open with genuine fan-photo footage but continue
straight into that song's intro and first few minutes of actual performance -- see "Cross-song
content bleed" for the general version of this failure mode, and the manual splice recipe
below for how it got untangled).

**Two real bugs found running this at scale, both fixed:**
1. `ffprobe -show_entries stream=width -of csv=p=0` returns a trailing comma even for a single
   requested field (e.g. `"3840,"`) -- `int()` on that raises. Request `width,height` together
   and split on comma instead of assuming a bare number comes back.
2. `is_portrait()` reads rotation METADATA, which is blind to a clip that's already been
   physically rotated (pixels correct, rotation tag intentionally absent/cleared, as happens
   when hand-fixing a sideways clip -- see the `transpose` recipe below). Such a clip has real
   `height > width` with no rotation tag at all. Fixed by falling back to actual aspect ratio
   (`is_portrait(path) or height > width`) wherever orientation is checked against a clip that
   might have been manually re-oriented.

**Manually fixing a clip with no rotation metadata but visibly sideways content**: when
`ffprobe`'s `side_data_list`/`rotate` tag is empty (not just wrong), there's nothing to toggle
-- the pixels themselves need physically rotating:
```powershell
ffmpeg -i in.MOV -vf "transpose=2" -c:v libx264 -preset fast -crf 14 -c:a copy `
  -map_metadata 0 -metadata creation_time=<original value, re-stamped explicitly> out.MOV
```
(`transpose=2` = 90° counterclockwise, `transpose=1` = 90° clockwise.) Always re-stamp
`creation_time` explicitly in the same command -- `-map_metadata 0` alone doesn't reliably
survive a filter pass -- and match the filesystem mtime afterward. Don't add a rotation tag
to the output; the pixels are already correctly oriented, so a tag would double-rotate it in
any player/tool that respects rotation metadata (this is exactly what bug #2 above has to
guard against downstream).

**Per-clip metadata lives in `Masters-Standardized\metadata\`** (moved there from a
pre-existing `Masters\metadata\` so `Masters\` stays original-files-only): one JSON per clip
per analysis (`<clip>-zoomglow.json`, `<clip>-blackout.json`, `<clip>-audioquality.json`, plus
whatever pre-existing per-clip dumps a show already had). Nothing here is consumed by
`build_song.py` yet -- it's groundwork for a future automated curation pass, reviewed by hand
for now.

## Never silently downgrade audio

Two rules, applied everywhere audio is touched (`build_song.py`'s scene assembly,
`preprocess_masters.py`, `analyze_audio_quality.py`):

**1. Resolve the audio stream explicitly -- never trust a bare `N:a` specifier or ffmpeg's
default stream pick.** iPhones recording with "Spatial Audio" turned on write a SECOND,
immersive multi-channel audio stream alongside the normal stereo mix; ffprobe reports it as
codec `unknown` (ffmpeg has no decoder for it). This is genuinely ambiguous to resolve
automatically:
- Letting ffmpeg's default output-stream heuristic choose was observed to silently pick the
  undecodable stream on some clips (loudnorm/astats both return nothing, no error) while
  working fine on sibling clips from the *same device* -- not deterministic, can't be trusted.
- A bare `N:a` stream specifier inside a filtergraph (e.g. `[0:a]` in `build_song.py`'s scene
  assembly, or in `load_wav()`'s cross-correlation sync step) is ambiguous the moment a clip has
  more than one audio stream -- a real, silent correctness risk for sync detection, not just an
  audio-quality one.

Fixed with one shared helper, `pick_audio_stream(path)` in `build_song.py` -- ffprobes every
stream, keeps only decodable audio ones (`codec_name` not `unknown`), and prefers an actual
2-channel stream over a higher channel count that would need an unplanned (and silently lossy)
downmix. Every script that touches a clip's audio calls this once and maps to that exact
stream index explicitly (`-map 0:{idx}`) -- `build_song.py`'s `load_clip_info()` (cached per
clip as `audio_idx`/`audio_channels`), its `load_wav()` cross-correlation step, and
`analyze_audio_quality.py`.

**2. Never re-encode audio that doesn't need touching, and never pull from an already-lossy
generation when a cleaner one exists.** `preprocess_masters.py`'s whole job is video treatment
(HDR tonemap, pillarbox/1K-in-4K) -- it used to re-encode audio to AAC anyway, as a side effect
of being one ffmpeg call per clip. Fixed to `-c:a copy` (bit-for-bit stream copy, explicit
`-map` to the resolved stream) so the review copies in `Masters-Standardized\` can never be a
quality downgrade from the source. Same principle is why `analyze_audio_quality.py` and any
future FLAC extraction read from `Masters\Standarized\` (raw originals) rather than
`Masters-Standardized\`'s `.mp4`s -- decoding an already-lossy AAC track and re-encoding it
again (even into a lossless FLAC container) gains nothing and adds a pointless extra
compression generation. The only point in the whole pipeline where lossy encoding should
happen at all is the very last step: muxing the final mastered FLAC into AAC 320k for the
video deliverables (see the goal statement at the top of this file).

## Fixed: audio/video desync in heavy-overlap scenes

Was confirmed with hard evidence on a scene with many long-overlapping contributor clips: a
1101.53s (33,046-frame) video track paired with a 657.825s audio track -- exactly the chosen
base clip's own raw duration. **443.7 seconds of video had zero corresponding audio.** Root
cause, two compounding issues in the old `build_scene_segment()`/`plan_scene_cutaways()`:

1. The audio track was a flat, unmodified playthrough of the base clip's own audio only
   (`atrim=0:base_dur`) -- completely decoupled from whatever the video track ended up doing.
2. Each cutaway was spliced into the video at its own FULL raw length, regardless of how long
   the real moment it was standing in for actually lasted. Fine for a brief cutaway -- the
   original design intent -- but when several long clips from different contributors clustered
   near the same offset (common on a heavily-covered song), each got queued back-to-back in
   full, serializing everyone's overlapping footage instead of cutting between simultaneous
   angles. The video ran far longer than the real wall-clock span, and the flat, independent
   audio track drifted out of sync with it almost immediately.

**The fix** (`pick_truth_audio()`, `resolve_song_units()`, `plan_scene_video()` in
`build_song.py`):
1. **Audio source-of-truth selection is decoupled from video base-selection.**
   `pick_truth_audio()` picks which clip's audio is a scene's continuous "truth" track using
   actual audio-quality metadata (`analyze_audio_quality.py`'s `is_clipped`/`peak_level_db`,
   tiered: confirmed-clean > unanalyzed > confirmed-clipping) -- not framing. This can
   legitimately pick a different clip than video base-selection would have.
2. **The song's audio timeline is built from the chosen truth-tracks**, crossfaded at scene
   boundaries, same mechanism as always -- the truth track's own unmodified audio plays
   through for its own real [start, end) span, which becomes that stretch's fixed clock.
3. **Video is fit to that audio clock, not the other way around.** `plan_scene_video()`
   partitions the truth track's real-world span into a sweep-line of sub-intervals (breakpoints
   at every candidate clip's own real overlap boundary, refined by the same timestamp-first/
   correlation-refined offset logic as always) and picks exactly one clip per sub-interval,
   trimmed to the REAL sub-span it covers -- never its own full raw length. Total video
   duration across the plan equals the truth track's own duration by construction, so it can
   never drift from the audio underneath it. Confirmed on the real Enter Sandman footage this
   was first found on: video/audio duration matched to within 9ms (343.733s vs 343.742s, down
   from a 443.7s gap) after the fix, visually spot-checked clean across the timeline.
4. **Exactly one clip plays per moment**, ranked landscape > steadiness
   (`shakiness-report.csv`, if present -- optional, best-effort, missing/stale entries just
   degrade to "unknown") > zoom/exposure quality (`detect_zoom_glow.py`'s flagged-transition
   count -- a whole-clip proxy for now, not yet localized to the specific sub-interval; a
   documented simplification, not a silent one) > longest. A real cutaway is always preferred
   over the truth track's own video when both are active for a moment -- truth's video is only
   a fallback for stretches nothing else covers. A candidate whose view of a moment is mostly
   blacked out (`detect_blackouts.py`) is excluded for that moment specifically (sub-interval
   granularity, via extra sweep-line breakpoints at each blackout interval's own boundary) --
   unless excluding it would leave no candidate at all, in which case it's kept rather than
   losing the only coverage of that moment (see "uniqueness beats quality" above).

**Orphan handling**: `build_scenes()` groups by TRANSITIVE overlap (A-B-C can be one scene even
if A and C never directly overlap), so a clip can belong to a scene yet fall entirely outside
whichever single clip quality picked as truth. `resolve_song_units()` peels such clips off and
re-groups them (recursively) among themselves as additional chronological units in the song,
instead of silently losing that footage.

Verified with a synthetic unit-test suite (8 cases covering all 4 fix items, the orphan path,
the blackout sub-interval granularity, and the lead-in/tail-out case below) before trusting it
on real footage, then on a real 960x540 smoke-test render of Enter Sandman (8 clips, the exact
song the original bug was found on) -- see the validated numbers in point 3 above.

**Follow-up gap, found by the user during the real post-build review this skill asks for (see
"Review before final assembly" discipline) and fixed the same night**: a clip that OVERLAPS
truth's window but also extends before truth["start"] (or past truth["end"]) is a subtler
version of the same silent-drop problem -- it passes the "covered" test (so it's not an
orphan), but `plan_scene_video()` only ever looks inside truth's own [start, end) span, so the
extending sliver still vanished. Caught live on "For Whom The Bell Tolls": the real start of
the song (a quiet bell toll before the band comes in) was only captured by a clip (`-E`) that
started 3s before the clip quality picked as truth (`-C`) -- the build's audio clock began at
truth's own start, silently starting cold on drums with no bell toll at all. Fixed the same way
as orphans, in `resolve_song_units()`: the earliest-starting lead-in (and symmetrically, the
latest-ending tail-out) is peeled off into its own small bracketing unit (`{"kind": "trim"}`),
trimmed to exactly the portion outside truth's window -- never the clip's full length, which
would duplicate the part already inside the main scene. (Separately, this investigation also
found an exact-duplicate clip: `13-14-Transition (Whiskey to Bell Tolls).MOV` turned out to be
the same recording as `14-...-E`, just a shorter trim, both starting at the identical second --
renamed with the `SKIP-` prefix rather than left to compete/duplicate in the scene.)

**Second follow-up gap, same lineage, found live on "Blackened" after splitting its own intro
out of the raw footage**: a lead-in/tail-out zone's "most coverage" clip (the one chosen for
`{"kind": "trim"}`) isn't always the WHOLE story either -- other short clips can be entirely
CONTAINED inside that same real-time zone without overlapping truth's own window at all, so
they fail the orphan test's only check (overlap with truth) while also never being the single
clip picked for the zone. They fell through as plain orphans and got resolved into their own
separate chronological units -- each replaying, in full, real time the zone's anchor clip had
already shown. Confirmed live: a scene's quality-ranked truth track was a short clip in the
MIDDLE of a long continuous recording's span; five shorter clips whose real time fell entirely
within that recording's lead-in got silently re-spliced in sequence AFTER the main span and its
tail -- the song's own opening visibly replayed itself ~3.5 minutes in (total output duration
372.67s, 86.5s of which was pure duplication). Fixed in `resolve_song_units()`: a lead-in/
tail-out zone now checks for orphans entirely contained in its own [zone_start, zone_end), and
if it finds any, promotes the whole zone from a flat `{"kind": "trim"}` (one clip, no choice) to
a proper `{"kind": "multi"}` -- same truth-anchored, one-candidate-per-moment machinery
(`plan_scene_video()`) as the main span, just anchored to the zone's own real span with the
zone's original "most coverage" clip as its continuous audio spine (`build_scene_segment()`
gained an `audio_real_start` field so a zone's audio can trim from a clip-local offset other
than 0 -- the tail-zone case, where the spine clip's own file-local start is BEFORE the zone
itself). No-op whenever a zone has no such contained orphans -- the overwhelmingly common case,
and produces the exact same output as before, clip-for-clip. Verified on the real rebuild: new
duration 286.17s (the 86.5s of duplication gone), A/V match within 21ms, and a pixel-diff scan
across the whole timeline confirmed the previously-duplicated clip now appears exactly once, in
its correct real-world position.

That same investigation also surfaced a quieter data gap worth knowing about: the two raw clips
created by physically splitting Blackened from its intro (new filenames, never analyzed before)
had no `analyze_audio_quality.py`/`detect_blackouts.py` output at all, so `pick_truth_audio()`
scored them as "unknown" rather than on real signal data -- not what actually misplaced the
footage above (the genuinely best-peak clip still legitimately won truth once all 9 clips were
re-analyzed on equal footing), but a reminder that **splitting or renaming a raw clip orphans
its metadata** unless the analysis passes are re-run afterward -- `<basename>-*.json` lookups
are purely by filename, with no fallback.

**Third follow-up gap, same lineage, found across THREE more songs by a systematic full-show
audit (not a single user-reported timestamp this time):** a clip that OVERLAPS a lead/tail zone
but isn't fully CONTAINED in it -- it extends past the zone's own edge -- fell through both
existing nets: the "contained orphan" fix above only looked for orphans fully inside the zone's
one "most coverage" anchor clip's own span, and the zone's own boundary was fixed to that same
anchor's edge. An overlapping-but-extending clip is neither "inside" (so it's not folded in) nor
"clear of everything" (so it's not a legitimate fresh orphan either) -- it just fell to the
generic orphan path and got replayed WHOLE, from scratch, as its own unrelated chronological
unit later in the song, silently duplicating everything it shared with the zone. Confirmed on
real footage: `10-The Unforgiven-B.MOV` (448.78s clip, ~432s of it a second showing of the
song's own first 7+ minutes -- output inflated from a real 475.78s to 905.49s), `14-For Whom The
Bell Tolls-B.MOV` (~212s duplicated), and `07-The Day That Never Comes-F.MOV` (21.24s
duplicated -- small enough, at ~10% of the song's own runtime, that a naive built-duration-vs-
real-span ratio check almost missed it; see the audit recipe below for why a single aggregate
ratio isn't sufficient).

**Fix:** rewrote the lead/tail zone construction in `resolve_song_units()` to derive the zone's
`[lo, hi)` boundaries directly from the scene's own real extent (gap-free by `build_scenes()`'s
construction, so this needs no "most coverage" anchor clip at all) and to select every clip that
OVERLAPS that span AT ALL -- same `clip_overlap()` test `covered` itself already uses against
truth's window -- rather than requiring full containment. There's no "contained vs. merely
overlapping" distinction left to fall through. Verified: 07/10/14 now compute output durations
that match their real clip-timeline span exactly (no residual overlap), with zero change to
every other song's computed units (01, 02-06, 08, 09, 12, 13, 15, 16 all produced byte-for-byte
identical unit breakdowns before and after).

**Audit recipe for finding this bug class across a whole show** (don't assume a fix proven on
one song is a no-op everywhere else -- verify it): (1) a cheap first pass, built-file duration
vs. the song's real clip-timeline span (`min(start)` to `max(end)` across its raw clips) --
flags the obvious cases (Unforgiven: 1.90x: builds badly inflated far past what any clip
actually covers), but a small duplication can hide inside a plausible-looking ratio (The Day
That Never Comes was only 1.03x and still had 21s of real duplication); (2) the real check is a
full `resolve_song_units()` trace per song, walking its units in order and checking each unit's
real `[start, end)` span against every PRIOR unit's span for the same song -- any overlap found
this way is duplicated footage, full stop, regardless of how small it looks against the total.
This same audit also caught that the `is_clipped`-schema fix noted above had only ever been
applied to Blackened's own 9 clips -- every other song's `analyze_audio_quality.py` output
show-wide was still on the stale pre-`is_clipped` schema, silently scoring every clip "clean"
regardless of real clipping. Fixed by rerunning `analyze_audio_quality.py` across all raw clips
in one pass, not just the clips of whichever song happened to be under investigation.

**Manual editorial overrides: `clip_override()`.** The automated signals (quality, steadiness,
zoom/glow, blackout) can't catch everything -- a false-negative zoom detection, or a genuinely
directorial call like "favor this camera during this performer's feature moment" that has
nothing to do with technical quality at all. `plan_scene_video()` takes an optional `overrides`
dict, keyed by clip basename (no extension), each value holding one or both of:
- `"only": (t0, t1)` -- this clip is NEVER a video candidate outside this clip-local window
  (hard exclude elsewhere). Found live: `detect_zoom_glow.py`'s person-bounding-box heuristic
  doesn't fire on a wide architecture/establishing shot with no prominent performer in frame, so
  it missed real zoom in/out on `02-Fuel.MOV` outside its clean, steady `[8s, 18s)` window.
- `"boost": (t0, t1)` -- this clip outranks every other candidate while inside this window, but
  doesn't exclude anyone else outside it (a soft preference, not a monopoly). Used to favor
  `05-72 Seasons-F`'s steady focus on Lars for its own first ~103s before it pans to James's
  solo -- a deliberate directorial choice, not something any quality metric would surface.

Breakpoints are added at override-window boundaries the same way they already are for blackout
intervals (`plan_scene_video()`'s sweep line) -- without that, a window boundary landing inside
an existing sub-interval couldn't actually take effect (the whole sub-interval would be judged
as one unit, not split at the override's own edges).

Threaded through as `build_song(..., overrides={...})`; this show's `run_v2.py` is where the
actual per-clip entries live (show-specific curation, not generic pipeline behavior).

**On "being a director"**: direct user feedback worth internalizing for any future curation
pass -- *"Remember you are a director and you can change cameras. That is the whole idea...
otherwise it becomes boring (but don't overdo it to be safe)."* The ranking's "stickiness" rule
(prefer the clip already playing on a tie) exists to avoid flicker-cuts from clock-jitter-sized
breakpoints, not to avoid legitimate cuts between simultaneously-good angles -- when multiple
cameras are genuinely active and comparable, occasionally favoring variety over staying locked
to one "best" clip is a real editorial goal, not yet something this pipeline does
automatically. The `overrides` mechanism is a manual stopgap for this (and for correcting
specific automated-signal misses), not a substitute for an eventual automatic variety pass --
flagged here as a real follow-up, not pursued yet because it deserves its own deliberate design
rather than a rushed bolt-on.

## Audio publish (`Build-Audio.ps1`)

Reads whatever `NN-Title.mp4` files are in `Videos\` (sorted numerically by prefix, so
`10-...` doesn't sort before `2-...`), and for each one:
- extracts a lossless FLAC,
- grabs a frame from the song's own video (center-square crop, darkened, title drawn on
  top, plus an optional `-Subtitle` line -- e.g. an artist/venue/date line),
  embeds it as the FLAC's cover art.

Then builds `00-FullShow.flac` by **re-encoding** audio straight out of the source videos
(never stream-copy-concatenating already-finished FLACs -- native FLAC has exactly one
STREAMINFO header per file, so a `-c copy` concat of several finished FLACs silently
truncates to the first track's duration). Its cover defaults to a frame from the longest
song (usually the most representative shot); override with `-FullShowCoverSrc <path>` if
there's a more iconic clip (e.g. a striking venue visual).

## Mastering (`Master-Audio.ps1`)

Optional post-process on the FLACs `Build-Audio.ps1` already made. Phone-in-a-crowd audio has
a characteristic problem set -- boomy/boxy low end (crowd rumble + mic proximity to the PA's
own bass-heavy mix), a dull/distant top end, and sometimes real digital clipping where two
already-hot phone clips get summed together (e.g. at a crossfade join) -- that a generic
"just normalize loudness" pass doesn't fix. This script runs, per file:

1. **SoX upsample** (48kHz -> 192kHz, `-NoUpsample` to skip) -- cheap insurance so any EQ band
   near the top of the audible range stays away from the processing Nyquist, not a fix for
   anything this source material lacks (a phone's own audio-for-video encoding already caps
   real content around ~18kHz).
2. **`adeclip`** -- repairs already-clipped samples. A limiter alone only prevents *new*
   clipping; it can't undo flat-topped samples already baked into the source.
3. **EQ**: highpass (rumble) -> bass cut (~100Hz, rebalances kick/bass vs. vocals+guitar when
   the low end is dominating the mix) -> a second, narrower cut (~450Hz, de-boxes without
   touching the ~100-250Hz kick/snare/tom body -- pushing this one too wide/deep is what
   thins out the drums) -> a presence bump (~3.5kHz) -> an air/cymbal shelf (~9kHz).
4. **Loudness + limiter**: normalizes to a *loud* target on purpose (default -10 LUFS /
   -0.3 dBTP) -- the point is "sounds like a great bootleg of a huge show," not a
   transparent, streaming-safe reference master. The limiter after loudnorm is a safety net,
   since single-pass loudnorm's true-peak estimate isn't exact.
5. **SoX downsample** back to 48kHz, final FLAC encode at max compression
   (`-compression_level 12`, lossless regardless of level -- just smaller/slower to encode),
   re-embedding the cover art and a full tag set (artist/album/date/genre/track number/etc.)
   in the same pass.

**Known-fixed bug: silent total mastering failure.** The EQ/loudnorm/limiter filter strings
are built by interpolating PowerShell variables directly into a double-quoted string, e.g.
`"equalizer=f=$BassCutHz:t=q:..."`. PowerShell parses `$VarName:` immediately followed by a
colon as a scope-qualified variable reference (the same syntax as `$env:TEMP`), **not** as "the
variable's value, then a literal colon" -- so every one of these interpolations silently
resolved to an empty string, producing filter strings like `equalizer=f==q:w=1:g=-4` that
ffmpeg rejected outright (`Undefined constant or missing '(' in '=q'`). Because the per-file
ffmpeg failure is caught and handled with `continue` (not a terminating error), the script's
own exit code stayed 0 -- **every song reported `[FAIL] processing` in the log, but the
orchestrating pipeline script never noticed and happily moved on to muxing/publishing the
untouched, unmastered FLACs**, meaning this had been silently never actually applying any
mastering at all. Fixed by wrapping each variable in `${...}` (`${BassCutHz}:t=q:...`) so the
brace closes the reference before the colon. If this script is ever copied/adapted elsewhere,
grep for `\$\w+:` in the new copy and fix the same way -- this is a general PowerShell gotcha,
not specific to this one chain. Always check the terminal output for `[FAIL]` rows after a
mastering run, the same way you already check `build_song.py`'s SUMMARY for `FFMPEG ERROR`.

**Known-fixed bug: cover art silently missing from every mastered FLAC.** `Build-Audio.ps1`
renders each song's cover PNG and `Master-Audio.ps1` re-embeds it during the final encode, but
the two scripts used to default to *different* folders for it -- `Build-Audio.ps1`'s `-CoverDir`
defaulted to a scratch folder under `$env:TEMP`, while `Master-Audio.ps1`'s defaulted to the
literal string `"Audio\AlbumArt"` (not derived from its own `-Audio` param, so pointing `-Audio`
at anything other than a folder literally named `Audio` -- e.g. a `AudioV3`-style rebuild --
made it look in the wrong place, or a folder that doesn't exist at all). Neither script errored;
`Master-Audio.ps1`'s cover-embed step just silently skipped, so every mastered FLAC published
with no embedded cover and nothing in the log said so. Fixed by making both scripts' `-CoverDir`
default to `""` and compute `Join-Path <their own audio-folder param> "AlbumArt"` at runtime, so
the two always agree without needing `-CoverDir` passed explicitly, whatever the folder's named
-- plus `Master-Audio.ps1` now prints a yellow warning if it still ends up with no cover folder.
If you ever need to backfill covers onto FLACs that already got mastered without them, you don't
need to re-run the mastering chain (that would double-process already-correct audio) -- grab a
frame from each song's video the same way `Make-Cover` does, then remux it in with `-c:a copy
-c:v png -disposition:v:0 attached_pic` (exactly what `Embed-Cover` does), which only adds the
cover stream and never touches the audio samples.

All the EQ/loudness numbers are parameters with the defaults above -- re-tune per show if the
balance sounds off; a quieter/more conservative streaming target is also reasonable (try
-14 LUFS instead of -10 if "loud" isn't the goal).

**After mastering, rebuild `00-FullShow.flac`** from the now-mastered per-song FLACs (concat +
re-encode, same rule as always -- never stream-copy-concat finished FLACs) rather than
re-deriving it from the original unmastered video audio, so the full-show track matches the
mastered individual songs.

## Requirements

- `ffmpeg`/`ffprobe` on PATH, built with `zscale`, `tonemap`, `adeclip`, and `h264_nvenc`
  (NVENC) support.
- An NVIDIA GPU for `h264_nvenc` -- every video encode in this skill (`build_song.py`,
  `build_fullshow_video.py`, `preprocess_masters.py`) uses it, deliberately with no CPU
  (`libx264`) fallback path (see "GPU encoding" below): CPU encoding was the original default
  and went unnoticed as a real bottleneck for a long time (0% NVENC utilization on a 4K,
  multi-song batch) simply because nothing ever checked. Decode and the filter chain itself
  (tonemap, blur, overlay, concat, xfade) still run on CPU either way -- only the final encode
  is GPU.
- `sox` on PATH (only needed for `Master-Audio.ps1`'s up/downsample steps). Audio processing
  (SoX, `adeclip`/EQ/loudnorm/limiter, FLAC encode) stays CPU-only -- there's no meaningful
  GPU path for any of it in ffmpeg, so this isn't a gap to close later, just the nature of
  that work.
- Python 3 with `numpy` (used for the audio cross-correlation sync).
- PowerShell (for `Build-Audio.ps1` and `Master-Audio.ps1`).

## GPU encoding

All three video-encoding call sites use `h264_nvenc` with a tuning pattern borrowed from the
proven `av1_nvenc` recipes in the `concert-video-pipeline` skill -- VBR+CQ rate control, `-bf 2
-b_ref_mode 2 -rc-lookahead 32 -g 240`. `h264` specifically, not `av1`: `build_fullshow_video.py`'s
per-song title-card pass has to stream-copy-concat its output with other already-H.264 segments
(including a show's own pre-titled intro, copied in untouched) at the very end -- mixing codecs
there would break that concat.

**Preset `p5` / `cq 23`, deliberately NOT that skill's own `p7` archival tuning.** First tried
`p7` (copying the other skill's settings directly) and CQ 20 (matching the CRF 20 it replaced)
-- measured SLOWER wall-clock than the `libx264 -preset fast` it was replacing on identical
phone-source content (1.47-1.87x vs. 1.93-2.23x on the same files). Root cause: `p7` is
NVENC's slowest/highest-quality tier, calibrated in the other skill for Nikon Z6III 4K HEVC
10-bit masters at ~190 Mbps -- genuinely detailed source worth every bit of encode time to
preserve. iPhone footage here never had that detail to begin with, so paying `p7`'s extra time
bought nothing visible, only a slower encode than the CPU path it was meant to beat.

Don't take that on faith either, though -- check the actual source, right there in
`Masters-Standardized\`/`Masters\Standarized\` (`ffprobe -select_streams v:0 -show_entries
stream=codec_name,width,height,bit_rate`). This show's real spread, phone to phone:

| clip | res | codec | source bitrate |
|---|---|---|---|
| 01-Blackened-I | 1080p | HEVC | 8.5 Mbps |
| 01-Blackened-J | 4K | HEVC | 59.4 Mbps |
| 01-Blackened-A | 4K | H264 | 35.9 Mbps |
| 15-One-E | 1080p | HEVC | 11.2 Mbps |
| 16-Enter Sandman-E | 1080p | HEVC | 10.9 Mbps |
| 05-72 Seasons-F | 1080p | HEVC | 11.3 Mbps |

~8.5-59 Mbps, a mix of true 4K and 1080p upscaled onto the shared 4K output canvas -- nowhere
near the Nikon pipeline's ~190 Mbps ceiling. `p5`/`cq 23` lands the actual re-encode output
around 17-19 Mbps at ~3.0x realtime on this content: comfortably inside what the footage itself
carries (not starving the genuine-4K clips, not wastefully exceeding the 1080p ones), confirmed
empirically on this show's own files, not inherited as a guess from the other skill's number
(which happened to land in a similar place, but for a reason -- Nikon CQ 23 was ALSO chosen by
measuring real output against real source, just a much higher-bitrate source). The general
lesson, not just a one-off number: **match the encode-quality tuning to the SOURCE's own
ceiling, verified against that source's actual measured bitrate -- never carry a quality
setting over from a different pipeline (or a different camera) without checking.**

No CPU fallback is intentional, not an oversight: a silent fallback to `libx264` on a machine
without NVENC would just quietly reintroduce the exact unnoticed-for-a-long-time slowdown this
section exists to prevent. If NVENC genuinely isn't available, let it fail loudly (ffmpeg's own
"Cannot load nvcuda.dll"/"No capable devices found" error) rather than silently degrading.

## Gotchas

- **Needs real `creation_time` metadata.** A clip that's been re-exported or re-encoded
  before reaching the Masters folder can lose this tag -- `build_song.py` raises a clear
  error naming the file rather than failing silently on a missing tag. Keep clips as close
  to straight-off-the-phone as possible.
- **Clock drift**: phone clocks aren't frame-exact, even on the same network. The
  timestamp-vs-correlation cross-check (see above) tolerates a few seconds of disagreement
  before trusting the timestamp over the correlation; if two phones' clocks are drifting by
  more than that, splice points within a true overlap scene may be slightly off (rare in
  practice -- phones resync over cellular/WiFi constantly).
- **GPU-based quality-assurance passes** (jitter/shakiness scoring via GPU phase
  correlation, a technique that reuses the exact same cross-correlation idea as the audio
  sync but in 2D) are a useful companion step but not part of this skill -- informational
  only, for deciding whether a particular clip is worth stabilizing.
- **RAM usage scales with clip count per song, badly.** A song's whole build is one ffmpeg
  process running a single `filter_complex` graph with every one of that song's clips open as
  a separate concurrent input -- nothing is flushed to disk between clips. Two things in that
  graph multiply per-frame memory hard: the HDR tonemap chain's `format=gbrpf32le` step
  (32-bit float, full-resolution RGB planes -- roughly a 66x jump from a normal 8-bit
  `yuv420p` frame, e.g. ~1.5MB -> ~99.5MB per frame at 4K) and `gblur=sigma=35` on the full 4K
  background plate (pillarbox and "1K-in-4K" compositing both use it). `xfade`/`acrossfade`
  add lookahead buffering on top (both sides of a transition decoded+tonemapped ahead of the
  fade), and multi-threaded decode adds a reorder buffer per input. For a song with many
  contributed clips from multiple people's phones (worst case seen so far: an Enter Sandman
  with four different contributors' footage overlapping), this combination pushed a single
  ffmpeg process past 100GB resident and got it killed by a host-level OOM guard during an
  otherwise-idle overnight run -- not a pipeline bug, just the graph's real footprint. Run
  `Watch-FfmpegMemory.ps1` alongside any `--all`/`Run-*-Pipeline.ps1` build on a box with this
  problem: it polls ffmpeg.exe's working set and, once it crosses a threshold (default
  100GiB), calls `EmptyWorkingSet()` (psapi.dll) on it -- nothing else, no throttling/kill --
  pushing its private pages out to the pagefile on demand instead of waiting for Windows' own
  trimmer, which can react slower than the guard that killed the overnight run. Cheap on NVMe
  if a trimmed page gets touched again. If a song's clip count is unusually high, also
  consider capping `-threads`/`-filter_threads` on that invocation, or restructuring to
  tonemap+write each clip to a temp file before compositing instead of one monolithic graph.
  Confirmed live against a real in-progress build: one manual `EmptyWorkingSet()` call dropped
  ffmpeg's resident set from 43.22GiB to 6.13GiB in well under a second, with no interruption
  to the running encode -- this is a solved problem for this skill now, just keep the watcher
  running for any clip-heavy multicam build.
