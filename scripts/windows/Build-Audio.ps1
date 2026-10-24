<#
Build-Audio.ps1 -- MLC-style audio publish, reusable across shows built by build_song.py.
Mirrors the concert-video-pipeline skill's 05-Publish-Audio.ps1 + flac-album-art conventions,
for a show whose final per-song videos already live in one folder (one file per song, named
"NN-Title.mp4" -- exactly what build_song.py's --all mode produces):

  Audio\<NN-Title>.flac   -- one per song, cover art = a frame from that song's own video
  Audio\00-FullShow.flac  -- every song concatenated in filename order, re-encoded (never
                             stream-copy-concat finished FLACs -- native FLAC has one
                             STREAMINFO header, so that silently truncates to track 1)

Covers: center-square frame from the song's video, darkened, title + an optional -Subtitle
line underneath (e.g. "Artist - Venue - Date"), embedded in place as "Cover (front)".

A video named "SKIP-*" (same convention as the concert-video-pipeline skill, e.g. a tuning
break or a bonus clip you don't want published) is left out of both the per-song output and
the full show.

Run from the show root (the folder containing Videos\ and where Audio\ should land):
    py path\to\Build-Audio.ps1 -Subtitle "Metallica - Las Vegas - Oct 17, 2026"
#>
param(
  [string]$Video = "Videos",
  [string]$Dst = "Audio",
  # Empty by default so it tracks $Dst (see below) -- defaulting this to a scratch/temp folder
  # instead caused a real bug: Master-Audio.ps1's own default CoverDir is "<Audio>\AlbumArt",
  # so covers rendered here under $env:TEMP were never where that script went looking, and
  # every mastered FLAC silently shipped with no embedded cover art. Pass -CoverDir explicitly
  # only if you want covers to land somewhere other than next to the FLACs they're embedded in.
  [string]$CoverDir = "",
  [string]$Subtitle = "",
  [string]$FullShowCoverSrc = "",
  [int]$Size = 1200,
  [int]$FontSize = 78,
  [int]$SubtitleFontSize = 36,
  [string]$FontFile = "C:/Windows/Fonts/arialbd.ttf",
  [int]$Compression = 12,
  [switch]$Force
)
$ErrorActionPreference = "Stop"
if (-not $CoverDir) { $CoverDir = Join-Path $Dst "AlbumArt" }

function Get-CreationTime {
  # Reads the real-world recording-start timestamp build_song.py now stamps on every
  # output video (see build_song.py's own creation_time fix). Returns $null if absent
  # (e.g. a video rendered before that fix) rather than failing the whole run.
  param([string]$VideoPath)
  # ffprobe prints NOTHING (not even a blank line) when the tag is entirely absent, so the
  # pipeline result is $null, not an empty string -- calling .Trim() on that directly throws
  # "cannot call a method on a null-valued expression" instead of hitting the `if ($ct)` guard
  # below. Confirmed on a title-carded file whose re-encode pass hadn't re-stamped the tag.
  $raw = & ffprobe -v error -show_entries format_tags=creation_time -of default=nk=1:nw=1 $VideoPath
  $ct = if ($raw) { $raw.Trim() } else { $null }
  if ($ct) { return $ct }
  return $null
}

function Set-FlacTimestamps {
  # Mirrors the real recording start into the FLAC's filesystem mtime, same reasoning as
  # build_song.py's os.utime() call -- so Explorer/file listings sort by when the song
  # actually happened, not when it was encoded.
  param([string]$FlacPath, [string]$CreationTime)
  if (-not $CreationTime) { return }
  $dt = [datetime]::ParseExact($CreationTime, "yyyy-MM-ddTHH:mm:ss.ffffffZ",
    [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AssumeUniversal)
  (Get-Item -LiteralPath $FlacPath).LastWriteTimeUtc = $dt
}

function Invoke-FfmpegWithRetry {
  param([string[]]$FfmpegArgs, [int]$MaxAttempts = 3)
  for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    & ffmpeg @FfmpegArgs
    if ($LASTEXITCODE -eq 0) { return $true }
  }
  return $false
}

function Make-Cover {
  # $At < 0 (the default) means "pick automatically" -- see pick_cover_frame.py: samples the
  # middle third of the video (never the first/last third, which can bleed in the previous or
  # next song -- see "Cross-song content bleed" in SKILL.md) and returns whichever sampled
  # frame's color is closest to that window's own average, so an unlucky single timestamp
  # (e.g. a lighting blackout at the exact midpoint) doesn't become the permanent cover. Pass
  # an explicit $At (0.0-1.0, fraction of total duration) only to force one specific moment.
  param([string]$SrcVideo, [string]$Title, [string]$CoverPng, [double]$At = -1)
  $dur = [double](& ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 $SrcVideo)
  if ($At -ge 0) {
    $t = $dur * $At
  } else {
    $picker = Join-Path $PSScriptRoot "pick_cover_frame.py"
    $t = [double](& py $picker $SrcVideo)
  }
  $titleFile = Join-Path $CoverDir "_title.txt"
  Set-Content -Path $titleFile -Value $Title -NoNewline -Encoding utf8
  $fontEsc = $FontFile -replace ':', '\:'
  $titleEsc = ($titleFile -replace '\\', '/') -replace ':', '\:'

  $boxH = if ($Subtitle) { 0.42 } else { 0.30 }
  $titleY = if ($Subtitle) { 0.72 } else { 0.80 }
  $vf = "crop=ih:ih,scale=${Size}:${Size}:flags=lanczos,format=rgb24," +
        "eq=brightness=-0.12:saturation=1.1," +
        "drawbox=x=0:y=ih*$(1-$boxH):w=iw:h=ih*${boxH}:color=black@0.45:t=fill," +
        "drawtext=fontfile='$fontEsc':textfile='$titleEsc':fontcolor=white:fontsize=${FontSize}:" +
        "borderw=4:bordercolor=black@0.8:x=(w-text_w)/2:y=h*${titleY}-text_h/2"
  if ($Subtitle) {
    $subFile = Join-Path $CoverDir "_subtitle.txt"
    Set-Content -Path $subFile -Value $Subtitle -NoNewline -Encoding utf8
    $subEsc = ($subFile -replace '\\', '/') -replace ':', '\:'
    $vf += ",drawtext=fontfile='$fontEsc':textfile='$subEsc':fontcolor=white:fontsize=${SubtitleFontSize}:" +
           "borderw=2:bordercolor=black@0.8:x=(w-text_w)/2:y=h*0.89-text_h/2"
  }
  $cArgs = @("-y", "-nostdin", "-hide_banner", "-loglevel", "error",
    "-ss", "$t", "-i", $SrcVideo, "-frames:v", "1", "-vf", $vf, $CoverPng)
  Invoke-FfmpegWithRetry $cArgs
}

function Embed-Cover {
  param([string]$FlacPath, [string]$CoverPng)
  $tmp = $FlacPath + ".cover-tmp.flac"
  $eArgs = @("-y", "-nostdin", "-hide_banner", "-loglevel", "error",
    "-i", $FlacPath, "-i", $CoverPng, "-map_metadata", "0",
    "-map", "0:a", "-map", "1", "-c:a", "copy", "-c:v", "png",
    "-metadata:s:v:0", "title=Cover", "-metadata:s:v:0", "comment=Cover (front)",
    "-disposition:v:0", "attached_pic", $tmp)
  if (Invoke-FfmpegWithRetry $eArgs) {
    Move-Item -LiteralPath $tmp -Destination $FlacPath -Force
    return $true
  }
  Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
  return $false
}

$VideoPath = (Resolve-Path $Video).Path
New-Item -ItemType Directory -Force -Path $Dst | Out-Null
New-Item -ItemType Directory -Force -Path $CoverDir | Out-Null
$DstPath = (Resolve-Path $Dst).Path

# Discover songs from the video folder: "NN-Title.mp4", sorted NUMERICALLY by the
# leading number (a plain string sort would put "10-..." before "2-...").
$videos = Get-ChildItem -LiteralPath $VideoPath -Filter "*.mp4" -File |
  Where-Object { $_.BaseName -notmatch '^SKIP-' } |
  ForEach-Object {
    if ($_.BaseName -match '^(\d+)-(.+)$') {
      [PSCustomObject]@{ Num = [int]$Matches[1]; Title = $Matches[2]; File = $_ }
    }
  } |
  Sort-Object Num
if (-not $videos) {
  Write-Host "No videos found in: $VideoPath (expected 'NN-Title.mp4')" -ForegroundColor Yellow
  exit 0
}

Write-Host "=== Individual song FLACs + covers ($($videos.Count) songs) ===" -ForegroundColor Yellow
foreach ($v in $videos) {
  $base = $v.File.BaseName
  $src = $v.File.FullName
  $out = Join-Path $DstPath "$base.flac"
  $cover = Join-Path $CoverDir "$base.png"

  if ((Test-Path $out) -and -not $Force) {
    Write-Host "SKIP (exists): $base.flac" -ForegroundColor Gray
    continue
  }

  Write-Host "Extracting: $base" -ForegroundColor Cyan
  $ct = Get-CreationTime -VideoPath $src
  $metaArgs = if ($ct) { @("-metadata", "creation_time=$ct") } else { @() }
  ffmpeg -y -hide_banner -loglevel error -i $src -vn -c:a flac -compression_level $Compression @metaArgs $out
  if ($LASTEXITCODE -ne 0) { Write-Host "  [FAIL] audio extract" -ForegroundColor Red; continue }

  Write-Host "  Cover: $($v.Title)"
  if (-not (Make-Cover -SrcVideo $src -Title $v.Title -CoverPng $cover)) {
    Write-Host "  [FAIL] cover render" -ForegroundColor Red; continue
  }
  if (Embed-Cover -FlacPath $out -CoverPng $cover) {
    Set-FlacTimestamps -FlacPath $out -CreationTime $ct
    Write-Host "  [OK] $base.flac" -ForegroundColor Green
  } else {
    Write-Host "  [FAIL] cover embed" -ForegroundColor Red
  }
}

Write-Host "`n=== 00-FullShow.flac ===" -ForegroundColor Yellow
$fullOut = Join-Path $DstPath "00-FullShow.flac"
if ((Test-Path $fullOut) -and -not $Force) {
  Write-Host "SKIP (exists): 00-FullShow.flac" -ForegroundColor Gray
} else {
  $listPath = Join-Path $CoverDir "_fullshow_concat.txt"
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  $lines = foreach ($v in $videos) { "file '$($v.File.FullName.Replace("'", "'\''"))'" }
  [System.IO.File]::WriteAllLines($listPath, $lines, $utf8NoBom)

  # Full show's creation_time is the very first song's -- the show's own real start.
  $showCt = Get-CreationTime -VideoPath $videos[0].File.FullName
  $fsMetaArgs = if ($showCt) { @("-metadata", "creation_time=$showCt") } else { @() }
  ffmpeg -y -hide_banner -loglevel error -stats -f concat -safe 0 -i $listPath `
    -vn -c:a flac -compression_level $Compression @fsMetaArgs $fullOut
  Remove-Item -Force $listPath -ErrorAction SilentlyContinue

  if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] 00-FullShow.flac" -ForegroundColor Red
  } else {
    $fsCover = Join-Path $CoverDir "00-FullShow.png"
    if ($FullShowCoverSrc) {
      $fsSrc = (Resolve-Path $FullShowCoverSrc).Path
    } else {
      # default: the longest song is usually the most representative/iconic shot
      $fsSrc = ($videos | Sort-Object { [double](& ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 $_.File.FullName) } -Descending | Select-Object -First 1).File.FullName
    }
    Make-Cover -SrcVideo $fsSrc -Title "Full Show" -CoverPng $fsCover -At 0.5 | Out-Null
    if (Embed-Cover -FlacPath $fullOut -CoverPng $fsCover) {
      Set-FlacTimestamps -FlacPath $fullOut -CreationTime $showCt
      Write-Host "  [OK] 00-FullShow.flac" -ForegroundColor Green
    } else {
      Write-Host "  [FAIL] cover embed on full show" -ForegroundColor Red
    }
  }
}

Write-Host "`nDone. Audio: $DstPath   Covers (png): $CoverDir" -ForegroundColor Green
