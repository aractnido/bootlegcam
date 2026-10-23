Reserved for macOS equivalents of the scripts in `scripts/windows/`.

Everything there currently assumes Windows: PowerShell-specific APIs in
`Watch-FfmpegMemory.ps1`, hardcoded `C:/Windows/Fonts/...` paths for the drawtext title
cards, and backslash path conventions throughout. None of that is fundamental to the
pipeline itself (ffmpeg, ffprobe, sox, and Python's numpy are all cross-platform), so a
macOS port is mostly about swapping those specifics, not rewriting the logic.
