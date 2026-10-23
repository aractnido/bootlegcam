<#
Watch-FfmpegMemory.ps1 -- proactive working-set trimmer for long multi-clip build_song.py
runs.

ffmpeg's filter-graph frame buffers are plain heap allocations (av_malloc/av_buffer_alloc),
not mmap'd or file-backed, so there's nothing for the OS to page out until its own memory
manager decides to trim under pressure -- which can react slower than a host-level OOM guard
watching resident memory (this is what killed an overnight V2 build: ffmpeg's own working set
crossed 100+GiB on a 128GiB box before Windows trimmed it). This script polls ffmpeg.exe's
working set and, once it crosses a threshold, calls EmptyWorkingSet() (psapi.dll) on it --
nothing else: no suspend, no throttle, no kill. That pushes its private pages out to the
pagefile/standby list on demand (both drives here are NVMe, so the round-trip if a trimmed
page gets touched again is cheap) instead of waiting.

Usage: run alongside build_song.py / Run-V2-Pipeline.ps1 in a separate window; it exits on its
own once no ffmpeg.exe process remains.

  Watch-FfmpegMemory.ps1 [-ThresholdGiB 100] [-PollSeconds 5] [-CooldownSeconds 15]
#>
param(
    [double]$ThresholdGiB = 100,
    [int]$PollSeconds = 5,
    [int]$CooldownSeconds = 15,
    [int]$MissesBeforeExit = 6   # consecutive empty polls before giving up -- a multi-song
                                 # pipeline has brief real gaps between one song's ffmpeg
                                 # exiting and the next one starting, which a single miss
                                 # mistook for "done" and exited early mid-run
)

Add-Type -MemberDefinition @'
[DllImport("psapi.dll", SetLastError=true)]
public static extern bool EmptyWorkingSet(IntPtr hProcess);
'@ -Name Psapi -Namespace Native

$thresholdBytes = $ThresholdGiB * 1GB
$lastTrim = [datetime]::MinValue
$misses = 0

Write-Host "Watching ffmpeg.exe working set -- threshold=$ThresholdGiB GiB, poll=${PollSeconds}s, cooldown=${CooldownSeconds}s" -ForegroundColor Cyan

while ($true) {
    $procs = Get-Process ffmpeg -ErrorAction SilentlyContinue
    if (-not $procs) {
        $misses++
        Write-Host "$(Get-Date) -- no ffmpeg.exe running ($misses/$MissesBeforeExit)" -ForegroundColor DarkYellow
        if ($misses -ge $MissesBeforeExit) {
            Write-Host "$(Get-Date) -- ffmpeg.exe absent for $MissesBeforeExit consecutive polls, exiting watcher." -ForegroundColor Yellow
            break
        }
        Start-Sleep -Seconds $PollSeconds
        continue
    }
    $misses = 0
    $totalWS = ($procs | Measure-Object -Property WorkingSet64 -Sum).Sum
    $totalGiB = [math]::Round($totalWS / 1GB, 2)
    $now = Get-Date

    if ($totalWS -ge $thresholdBytes -and ($now - $lastTrim).TotalSeconds -ge $CooldownSeconds) {
        Write-Host "$now -- ffmpeg WS=$totalGiB GiB >= $ThresholdGiB GiB, emptying working set..." -ForegroundColor Magenta
        foreach ($p in $procs) {
            try {
                [Native.Psapi]::EmptyWorkingSet($p.Handle) | Out-Null
            } catch {
                Write-Host "  trim failed for PID $($p.Id): $_" -ForegroundColor Red
            }
        }
        Start-Sleep -Milliseconds 500
        $procs2 = Get-Process ffmpeg -ErrorAction SilentlyContinue
        $afterGiB = if ($procs2) { [math]::Round((($procs2 | Measure-Object -Property WorkingSet64 -Sum).Sum) / 1GB, 2) } else { 0 }
        Write-Host "  WS after trim: $afterGiB GiB" -ForegroundColor Green
        $lastTrim = $now
    } else {
        Write-Host "$now -- ffmpeg WS=$totalGiB GiB" -ForegroundColor DarkGray
    }

    Start-Sleep -Seconds $PollSeconds
}
