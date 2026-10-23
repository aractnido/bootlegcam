# bootlegcam 🎸📱

Turn everyone's shaky phone videos from the show into one clean bootleg.

You and a few other people filmed a concert on your phones. Different angles, different songs,
no shared timecode, nobody hit record at the same moment. 🎶 `bootlegcam` takes that whole messy
folder and turns it into four things. A clean multicam edit per song, cutting between whoever
had the best angle at each moment. One continuous full-show video with a title card per song. A
mastered, lossless FLAC per song (de-clipped, EQ'd, loud like a great bootleg should be, with
embedded cover art 🖼️). And the whole set as one continuous lossless FLAC.

This is an agentic skill. `SKILL.md` holds the agent's instructions, written for an AI agent to
read and drive the scripts in `scripts/`. 🤖 You bring the footage and tell the agent which raw
clip belongs to which song, and it figures out the sync, the editing, and the mastering.

## Why this is hard 🧩

Phone footage from a real concert is messy in specific, predictable ways. There's no shared
timecode, so two people's phones pointed at the stage at the "same moment" are actually offset
by however long it took each of them to hit record. Clips aren't simultaneous multi-angle
coverage nearly as often as you'd think either, since just as often it's one person's recording
broken into several files because they stopped and restarted the camera app.

On top of that there's the format mess. iPhones shoot HDR that most players can't decode
correctly out of the box, portrait clips need to live on a landscape canvas without ugly black
bars, and phone mics clip and boom and lose the top end in a loud venue 🔊. Someone's phone also
keeps rolling past the end of a song into the next one while someone else's clip cuts out right
as the real moment starts.

`bootlegcam` groups clips into real-world scenes by their actual recording timestamp first, then
picks whichever clip has the best audio signal as the source-of-truth track for each scene,
independent of which clip looks best, and fits video to that audio's clock rather than the other
way around. Read `SKILL.md` for the full reasoning, the bugs that got found fixing this on real
footage, and why each design choice exists.

## Requirements ⚙️

You'll need `ffmpeg`/`ffprobe` on PATH built with `zscale`, `tonemap`, `adeclip`, and
`h264_nvenc` support, plus an NVIDIA GPU, since every video encode uses it with no CPU fallback.
Audio mastering needs `sox` on PATH. The sync logic needs Python 3 with `numpy`, and the audio-
publishing scripts need PowerShell.

## Quick start 🚀

See `SKILL.md` for the full walkthrough. Short version, from your show's root folder.

```powershell
py scripts\build_song.py Masters --all Videos --metadata-dir Masters-Standardized\metadata
scripts\Build-Audio.ps1 -Subtitle "Artist - Venue - Date"
scripts\Master-Audio.ps1 -Artist "Band Name" -Album "Live at Venue" -Date "YYYY-MM-DD"
scripts\Mux-MasteredAudio.ps1 -Video Videos -Audio Audio
py scripts\build_fullshow_video.py Videos "Videos\FullShow-Video.mp4"
```

