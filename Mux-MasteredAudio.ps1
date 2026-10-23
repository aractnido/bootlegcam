<#
Mux-MasteredAudio.ps1 -- replaces each per-song video's raw/unmastered audio track with the
corresponding mastered FLAC from Master-Audio.ps1, so the finished video also carries the
EQ'd/loudness-corrected audio, not just the standalone FLAC deliverable.

The video stream is never re-encoded (-c:v copy) -- this is a fast container remux, not a
quality-affecting pass. Run this AFTER Build-Audio.ps1 + Master-Audio.ps1 have both finished,
and BEFORE build_fullshow_video.py (so the full-show compilation and its title-card pass
inherit the mastered audio for free instead of needing a second audio swap).

Usage:
    Mux-MasteredAudio.ps1 -Video VideosV2 -Audio AudioV2
#>
param(
  [string]$Video = "Videos",
  [string]$Audio = "Audio"
)
$ErrorActionPreference = "Stop"
$VideoPath = (Resolve-Path $Video).Path
$AudioPath = (Resolve-Path $Audio).Path

$videos = Get-ChildItem -LiteralPath $VideoPath -Filter "*.mp4" -File |
  Where-Object { $_.BaseName -match '^\d+-' }

foreach ($v in $videos) {
  $base = $v.BaseName
  $flac = Join-Path $AudioPath "$base.flac"
  if (-not (Test-Path -LiteralPath $flac)) {
    Write-Host "SKIP (no mastered audio found): $base" -ForegroundColor Yellow
    continue
  }
  $tmp = $v.FullName + ".audio-tmp.mp4"
  # -map_metadata 0 keeps the VIDEO's own container metadata (notably creation_time, which
  # build_song.py stamps) -- not the FLAC's tag set, which is about the audio deliverable,
  # not the video file.
  # 320k AAC -- matches concert-video-pipeline's own standard (see SKILL.md's goal statement
  # for why: the highest-quality-without-sacrificing-compatibility point for audio muxed into
  # an MP4/MOV container).
  ffmpeg -y -v error -i $v.FullName -i $flac -map 0:v -map 1:a -c:v copy -c:a aac -b:a 320k -map_metadata 0 $tmp
  if ($LASTEXITCODE -eq 0) {
    Move-Item -LiteralPath $tmp -Destination $v.FullName -Force
    Write-Host "OK: $base" -ForegroundColor Green
  } else {
    Write-Host "FAIL: $base" -ForegroundColor Red
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
  }
}
Write-Host "Done." -ForegroundColor Green
