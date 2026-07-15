<#
.SYNOPSIS
  Repository-contained MANUAL launcher for the ClearWright control plane.

.DESCRIPTION
  Starts the local control-plane server with absolute path resolution, explicit
  queue root / mode / bind / port, redirected and rotated stdout/stderr logs,
  and race-safe duplicate-process protection. The SERVER is authoritative for
  single-instance and occupied-port refusal (via its instance lock and bind);
  this launcher's port pre-check is a fast convenience only.

  It registers NOTHING: no scheduled task, no service, no startup persistence,
  no elevation, and no secrets on the command line (the server reads
  OPENAI_API_KEY from the environment itself).

  Exit codes: 0 started | 2 python not found | 3 port occupied |
              4 health timeout | 5 already running.

.NOTES
  Stabilization work item message:msg-20260715T033322041191. Manual use only.
#>
[CmdletBinding()]
param(
    [string]$PythonPath,
    [string]$QueueRoot = 'D:\AI-Agents\ClearWright\runtime\queues\active',
    [ValidateSet('operator', 'demo')][string]$Mode = 'operator',
    [string]$BindHost = '127.0.0.1',
    [int]$Port = 8787,
    [int]$HealthTimeoutSec = 20
)
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Server = Join-Path $RepoRoot 'apps\control-plane\server.py'
# Canonical control-artifact directory: <parent of queue root>\logs
$LogsDir = Join-Path (Split-Path -Parent (Resolve-Path -LiteralPath $QueueRoot -ErrorAction SilentlyContinue).Path) 'logs'
if (-not $LogsDir -or $LogsDir -eq '\logs') {
    $LogsDir = Join-Path (Split-Path -Parent ([System.IO.Path]::GetFullPath($QueueRoot))) 'logs'
}
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

$LaunchLock = Join-Path $LogsDir ("launcher-{0}.launchlock" -f $Port)
$PidFile    = Join-Path $LogsDir ("clearwright-{0}.pid" -f $Port)
$StageFile  = Join-Path $LogsDir ("clearwright-{0}.staging" -f $Port)
$OutLog     = Join-Path $LogsDir 'control-plane.out.log'
$ErrLog     = Join-Path $LogsDir 'control-plane.err.log'

function Rotate-Log([string]$Path, [int]$MaxBytes = 10485760, [int]$Keep = 5) {
    if ((Test-Path -LiteralPath $Path) -and ((Get-Item -LiteralPath $Path).Length -ge $MaxBytes)) {
        for ($i = $Keep - 1; $i -ge 1; $i--) {
            $older = "$Path.$i"; $newer = "$Path.$($i + 1)"
            if (Test-Path -LiteralPath $older) { Move-Item -LiteralPath $older -Destination $newer -Force }
        }
        Move-Item -LiteralPath $Path -Destination "$Path.1" -Force
    }
}

# (1) Atomic launch lock BEFORE any pre-check, closing the two-starts race.
try {
    $lockStream = [System.IO.File]::Open($LaunchLock, [System.IO.FileMode]::CreateNew,
                                         [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
} catch {
    # A launchlock older than 120s whose creator is gone is stale; remove it.
    if ((Test-Path -LiteralPath $LaunchLock) -and
        ((Get-Date) - (Get-Item -LiteralPath $LaunchLock).LastWriteTime).TotalSeconds -gt 120) {
        Remove-Item -LiteralPath $LaunchLock -Force
        $lockStream = [System.IO.File]::Open($LaunchLock, [System.IO.FileMode]::CreateNew,
                                             [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    } else {
        Write-Error "Another launch is in progress (launchlock held)."
        exit 5
    }
}

try {
    # (2) Resolve python to a DIRECT executable (not the 'py' launcher wrapper)
    # so the spawned process IS the server and the recorded PID is stoppable.
    if (-not $PythonPath) {
        $direct = Get-Command python -ErrorAction SilentlyContinue
        if ($direct) {
            $PythonPath = $direct.Source
        } else {
            $py = Get-Command py -ErrorAction SilentlyContinue
            if ($py) { $PythonPath = 'py -3.12' }  # wrapper fallback
        }
    }
    if (-not $PythonPath) { Write-Error 'Python not found.'; exit 2 }
    $pyExe = ($PythonPath -split ' ')[0]
    if (-not (Test-Path -LiteralPath $pyExe) -and -not (Get-Command $pyExe -ErrorAction SilentlyContinue)) {
        Write-Error "Python '$pyExe' not found."; exit 2
    }

    # (3) Rotate logs.
    Rotate-Log $OutLog; Rotate-Log $ErrLog

    # (4) Convenience port pre-check (server is authoritative).
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        Write-Error "Port $Port is already in use."; exit 3
    }
    if (Test-Path -LiteralPath $PidFile) {
        $existing = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
        if ($existing.pid -and (Get-Process -Id $existing.pid -ErrorAction SilentlyContinue)) {
            Write-Error "ClearWright already running (pid $($existing.pid))."; exit 5
        }
    }

    # (5) Spawn detached with redirected streams; stage the launch identity.
    $pyArgs = @($Server, '--port', "$Port", '--mode', $Mode, '--queue-root', $QueueRoot)
    if ($BindHost -ne '127.0.0.1') { $pyArgs += @('--host', $BindHost) }
    $proc = Start-Process -FilePath $pyExe `
        -ArgumentList (@($PythonPath -split ' ' | Select-Object -Skip 1) + $pyArgs) `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
    @{ pid = $proc.Id; server = $Server; queue_root = $QueueRoot; port = $Port; host = $BindHost } |
        ConvertTo-Json | Set-Content -LiteralPath $StageFile -Encoding UTF8

    # (6) Poll health.
    $deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
    $ok = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        try {
            $h = Invoke-RestMethod "http://$BindHost`:$Port/api/health" -TimeoutSec 3
            if ($h.ok) { $ok = $true; break }
        } catch { }
    }
    if ($ok) {
        Move-Item -LiteralPath $StageFile -Destination $PidFile -Force
        Write-Output "ClearWright started (pid $($proc.Id)) on http://$BindHost`:$Port"
        exit 0
    } else {
        if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) { Stop-Process -Id $proc.Id -Force }
        Remove-Item -LiteralPath $StageFile -Force -ErrorAction SilentlyContinue
        Write-Error "Server did not become healthy within $HealthTimeoutSec s."; exit 4
    }
} finally {
    $lockStream.Close()
    Remove-Item -LiteralPath $LaunchLock -Force -ErrorAction SilentlyContinue
}
