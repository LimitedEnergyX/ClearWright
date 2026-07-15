<#
.SYNOPSIS
  Clean-stop helper for the ClearWright control plane started by
  start-clearwright.ps1.

.DESCRIPTION
  Verifies the recorded process is genuinely the ClearWright server (executable
  path, server.py, queue root, and port all match) BEFORE any termination, then
  requests a graceful stop via the filesystem-local stop sentinel (no HTTP stop
  route). If the process does not exit within the deadline it falls back to
  Stop-Process; a forced stop leaves no shutdown_graceful record, so the next
  start records prior_unclean_shutdown -- this is documented, not hidden.

  Exit codes: 0 stopped | 2 not running | 6 identity mismatch (refused).

.NOTES
  Stabilization work item message:msg-20260715T033322041191. Manual use only.
#>
[CmdletBinding()]
param(
    [string]$QueueRoot = 'D:\AI-Agents\ClearWright\runtime\queues\active',
    [int]$Port = 8787,
    [int]$GraceSeconds = 15
)
$ErrorActionPreference = 'Stop'

$LogsDir = Join-Path (Split-Path -Parent ([System.IO.Path]::GetFullPath($QueueRoot))) 'logs'
$PidFile = Join-Path $LogsDir ("clearwright-{0}.pid" -f $Port)
$Sentinel = Join-Path $LogsDir ("clearwright-{0}.stop" -f $Port)

if (-not (Test-Path -LiteralPath $PidFile)) {
    Write-Output 'ClearWright is not running (no pid file).'; exit 2
}
$rec = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
$procId = [int]$rec.pid
$proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
if (-not $proc) {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Write-Output "Recorded process (pid $procId) is not running; cleaned pid file."; exit 2
}

# Identity verification via the command line BEFORE any termination.
$cim = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
$cmd = if ($cim) { $cim.CommandLine } else { '' }
$serverOk = $cmd -match [regex]::Escape('server.py')
$queueOk  = $cmd -match [regex]::Escape($QueueRoot)
$portOk   = $cmd -match ("--port\s+{0}\b" -f $Port)
if (-not ($serverOk -and $queueOk -and $portOk)) {
    Write-Error "REFUSED: pid $procId does not match the ClearWright server (server.py/queue/port). Not terminating."
    exit 6
}

# Graceful stop via the sentinel (server polls it, logs shutdown_graceful).
$startTime = $proc.StartTime.ToUniversalTime().ToString('o')
@{ pid = $procId; start_time = $startTime; at = (Get-Date).ToUniversalTime().ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath $Sentinel -Encoding UTF8

$deadline = (Get-Date).AddSeconds($GraceSeconds)
while ((Get-Date) -lt $deadline) {
    if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        Write-Output "ClearWright stopped gracefully (pid $procId)."; exit 0
    }
    Start-Sleep -Milliseconds 500
}

# Fallback: force stop. Next start will record prior_unclean_shutdown.
Remove-Item -LiteralPath $Sentinel -Force -ErrorAction SilentlyContinue
Stop-Process -Id $procId -Force
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
Write-Output "ClearWright force-stopped (pid $procId); next start will record prior_unclean_shutdown."
exit 0
