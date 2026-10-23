<#
Master-Audio.ps1 -- post-process the FLACs that Build-Audio.ps1 already extracted, applying
a mastering chain tuned for phone-recorded concert audio: a crowd-mic'd phone picks up a
boomy, boxy low end, a dull/distant top end (and sometimes outright digital clipping from
the phone's own limiter, especially right at crossfade joins), with nowhere near the clarity
of the venue's own PA. This script corrects that, deliberately mastered LOUD (close to a
modern rock-record level) rather than streaming-safe quiet, since the point is "sounds like a
great bootleg of a huge show," not a transparent reference recording.

Chain, per file: SoX upsample -> ffmpeg (declip, EQ, loudness, limiter) -> SoX downsample ->
final FLAC encode (re-embeds cover art + full metadata tag set in the same pass).

Why SoX for the resample step specifically: its "very high quality" resampler has very low
ripple/aliasing, which matters if this chain is ever pointed at cleaner, less band-limited
source material than typical phone-concert audio (the upsample step itself is a precaution,
not a fix -- it won't recover detail a phone's audio pipeline never captured).

Why upsample/downsample at all around the EQ: keeps the parametric EQ's bilinear-transform
warping away from the processing Nyquist for any future filter added near the top of the
audible range, and gives the limiter more precision for inter-sample peaks. Cheap insurance,
not a dramatic quality jump on already band-limited phone audio.

Why `adeclip` specifically (not just a limiter): a limiter only prevents NEW clipping going
forward -- it can't repair samples that are already flat-topped in the source. `adeclip`
interpolates through already-clipped runs. Found this necessary on a real file where two
already-hot phone clips summed over 0dBFS at a crossfade join.

Usage (run from the show root, after Build-Audio.ps1):
    Production\Scripts\bootlegcam\Master-Audio.ps1 `
        -Artist "Metallica" -Album "Live at The Sphere, Las Vegas" -Date "2026-10-17" `
        -Organization "Blackened Recordings" -Location "The Sphere, Las Vegas, NV"

Requires: sox and ffmpeg (with the adeclip filter) on PATH.
#>
param(
  [string]$Audio = "Audio",
  # Empty by default so it tracks $Audio (see below). A hardcoded literal here used to mean
  # that pointing -Audio at a folder other than the literal "Audio" (e.g. a VideosV3-style
  # rebuild's "AudioV3") left this still looking at "Audio\AlbumArt" -- the wrong folder, or
  # one that doesn't exist -- and every song got mastered with no cover art, silently.
  [string]$CoverDir = "",

  # Identity / tagging -- fill in for your own show; Comment stays honest about provenance
  # so this is never mistaken for an actual official release if ever shared.
  [string]$Artist = "",
  [string]$AlbumArtist = "",
  [string]$Album = "",
  [string]$Date = "",
  [string]$Genre = "Metal",
  [string]$Organization = "",
  [string]$Location = "",
  [string]$Comment = "Fan-shot multicam recording -- personal archive, not an official release",

  # EQ recipe -- defaults are what worked for a phone-in-a-crowd recording at a very loud
  # arena/dome show; re-tune per venue/recording if the balance sounds off (see SKILL.md).
  [int]$HighpassHz = 50,
  [int]$BassCutHz = 100,   [double]$BassCutDb = -4,     # rebalances kick/bass vs. vocals+guitar
  [int]$BoxCutHz = 450,    [double]$BoxCutQ = 1.2,  [double]$BoxCutDb = -6,  # de-box, drum-safe
  [int]$PresenceHz = 3500, [double]$PresenceDb = 3,      # recovers "click"/presence
  [int]$AirHz = 9000,      [double]$AirDb = 5,           # cymbal/air shelf

  # Loudness -- mastered loud on purpose (see module docstring)
  [double]$TargetLUFS = -10,
  [double]$TargetTP = -0.3,
  [double]$LRA = 6,
  [double]$LimiterCeiling = 0.97,

  [switch]$NoUpsample,
  [int]$UpsampleRate = 192000,
  [int]$Compression = 12,

  [string]$Only = ""   # process a single file's basename (no extension) instead of the whole folder
)
$ErrorActionPreference = "Stop"

function Require-Command($name) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Missing '$name' on PATH." }
}
Require-Command sox
Require-Command ffmpeg

$AudioPath = (Resolve-Path $Audio).Path
if (-not $CoverDir) { $CoverDir = Join-Path $Audio "AlbumArt" }
$CoverPath = if (Test-Path $CoverDir) { (Resolve-Path $CoverDir).Path } else { $null }
if (-not $CoverPath) {
  Write-Host "WARNING: no cover-art folder at '$CoverDir' -- FLACs will be (re)written with NO embedded cover. Run Build-Audio.ps1 first (it writes covers there by default), or pass -CoverDir." -ForegroundColor Yellow
}

$eqChain = "highpass=f=$HighpassHz," +
           "equalizer=f=${BassCutHz}:t=q:w=1:g=$BassCutDb," +
           "equalizer=f=${BoxCutHz}:t=q:w=${BoxCutQ}:g=$BoxCutDb," +
           "equalizer=f=${PresenceHz}:t=q:w=1:g=$PresenceDb," +
           "treble=f=${AirHz}:g=$AirDb"
$loudnorm = "loudnorm=I=${TargetLUFS}:TP=${TargetTP}:LRA=$LRA"
$limiter = "alimiter=limit=${LimiterCeiling}:attack=5:release=50"

$allFiles = Get-ChildItem -LiteralPath $AudioPath -Filter "*.flac" |
  Where-Object { -not $Only -or $_.BaseName -eq $Only } | Sort-Object Name
# 00-FullShow is rebuilt AFTER the loop below, by concatenating the now-mastered per-song
# files -- never mastered on its own. Two reasons: running loudnorm across one giant
# concatenated file gives a different (wrong) result than per-song normalization, and
# mastering it independently here would just be thrown away once it's rebuilt anyway.
$files = $allFiles | Where-Object { $_.BaseName -ne "00-FullShow" }
$rebuildFullShow = -not $Only -and ($allFiles | Where-Object { $_.BaseName -eq "00-FullShow" })
$total = $files.Count
$trackNum = 0

foreach ($f in $files) {
  $name = $f.BaseName
  $trackNum++
  $title = ($name -replace '^\d+-', '')
  Write-Host "=== $name ===" -ForegroundColor Cyan

  $tmp = New-Item -ItemType Directory -Force -Path (Join-Path $env:TEMP "master-audio-$([guid]::NewGuid().ToString('N'))")
  try {
    $src = $f.FullName
    # Read before the sox/ffmpeg WAV round-trip below, which carries no metadata at all --
    # re-applied explicitly in the final encode's $metaArgs so mastering doesn't silently
    # drop the real recording-start timestamp Build-Audio.ps1 already stamped.
    $srcCt = (& ffprobe -v error -show_entries format_tags=creation_time -of default=nk=1:nw=1 $src).Trim()
    if ($NoUpsample) {
      $up = $src
    } else {
      $up = Join-Path $tmp "up.wav"
      & sox $src -r $UpsampleRate $up
    }

    $proc = Join-Path $tmp "proc.wav"
    $af = "adeclip,$eqChain,$loudnorm,$limiter"
    ffmpeg -y -v error -i $up -af $af $proc
    if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] processing" -ForegroundColor Red; continue }

    if ($NoUpsample) {
      $down = $proc
    } else {
      $down = Join-Path $tmp "down.wav"
      & sox $proc -r 48000 $down
    }

    $cover = if ($CoverPath) { Join-Path $CoverPath "$name.png" } else { $null }
    $metaArgs = @(
      "-metadata", "title=$title",
      "-metadata", "artist=$Artist",
      "-metadata", "album_artist=$AlbumArtist",
      "-metadata", "album=$Album",
      "-metadata", "date=$Date",
      "-metadata", "genre=$Genre",
      "-metadata", "organization=$Organization",
      "-metadata", "location=$Location",
      "-metadata", "comment=$Comment"
    )
    $metaArgs += @("-metadata", "track=$trackNum/$total")
    if ($srcCt) { $metaArgs += @("-metadata", "creation_time=$srcCt") }

    $tmpOut = "$src.master-tmp.flac"
    $cmd = @("-y", "-v", "error", "-i", $down)
    if ($cover -and (Test-Path -LiteralPath $cover)) {
      $cmd += @("-i", $cover, "-map", "0:a", "-map", "1", "-c:v", "png",
                "-metadata:s:v:0", "title=Cover", "-metadata:s:v:0", "comment=Cover (front)",
                "-disposition:v:0", "attached_pic")
    }
    $cmd += @("-c:a", "flac", "-compression_level", "$Compression") + $metaArgs + @($tmpOut)
    ffmpeg @cmd
    if ($LASTEXITCODE -eq 0) {
      Move-Item -LiteralPath $tmpOut -Destination $src -Force
      if ($srcCt) {
        $dt = [datetime]::ParseExact($srcCt, "yyyy-MM-ddTHH:mm:ss.ffffffZ",
          [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AssumeUniversal)
        (Get-Item -LiteralPath $src).LastWriteTimeUtc = $dt
      }
      Write-Host "  [OK]" -ForegroundColor Green
    } else {
      Write-Host "  [FAIL] final encode" -ForegroundColor Red
    }
  } finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  }
}

if ($rebuildFullShow) {
  Write-Host "=== Rebuilding 00-FullShow.flac from mastered per-song files ===" -ForegroundColor Yellow
  $fullShowPath = Join-Path $AudioPath "00-FullShow.flac"
  $listPath = Join-Path $env:TEMP "master-audio-fullshow-$([guid]::NewGuid().ToString('N')).txt"
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  $lines = foreach ($f in $files) { "file '$($f.FullName.Replace("'", "'\''"))'" }
  [System.IO.File]::WriteAllLines($listPath, $lines, $utf8NoBom)

  $earliestCt = ($files | ForEach-Object {
    (& ffprobe -v error -show_entries format_tags=creation_time -of default=nk=1:nw=1 $_.FullName).Trim()
  } | Where-Object { $_ } | Sort-Object)[0]

  $fsCover = if ($CoverPath) { Join-Path $CoverPath "00-FullShow.png" } else { $null }
  $fsMetaArgs = @(
    "-metadata", "title=Full Show",
    "-metadata", "artist=$Artist",
    "-metadata", "album_artist=$AlbumArtist",
    "-metadata", "album=$Album",
    "-metadata", "date=$Date",
    "-metadata", "genre=$Genre",
    "-metadata", "organization=$Organization",
    "-metadata", "location=$Location",
    "-metadata", "comment=$Comment"
  )
  if ($earliestCt) { $fsMetaArgs += @("-metadata", "creation_time=$earliestCt") }

  $tmpOut = "$fullShowPath.rebuild-tmp.flac"
  $cmd = @("-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", $listPath)
  if ($fsCover -and (Test-Path -LiteralPath $fsCover)) {
    $cmd += @("-i", $fsCover, "-map", "0:a", "-map", "1", "-c:v", "png",
              "-metadata:s:v:0", "title=Cover", "-metadata:s:v:0", "comment=Cover (front)",
              "-disposition:v:0", "attached_pic")
  } else {
    $cmd += @("-map", "0:a")
  }
  $cmd += @("-c:a", "flac", "-compression_level", "$Compression") + $fsMetaArgs + @($tmpOut)
  ffmpeg @cmd
  Remove-Item -Force $listPath -ErrorAction SilentlyContinue
  if ($LASTEXITCODE -eq 0) {
    Move-Item -LiteralPath $tmpOut -Destination $fullShowPath -Force
    if ($earliestCt) {
      $dt = [datetime]::ParseExact($earliestCt, "yyyy-MM-ddTHH:mm:ss.ffffffZ",
        [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AssumeUniversal)
      (Get-Item -LiteralPath $fullShowPath).LastWriteTimeUtc = $dt
    }
    Write-Host "  [OK] 00-FullShow.flac rebuilt from mastered tracks" -ForegroundColor Green
  } else {
    Write-Host "  [FAIL] 00-FullShow.flac rebuild" -ForegroundColor Red
  }
}
Write-Host "Done." -ForegroundColor Green
