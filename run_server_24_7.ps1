# Stratos Poker 24/7 watchdog.
# Started hidden by the scheduled task "StratosPoker_Server" (at logon, re-checked every 10 minutes).
# - Runs the poker server on 127.0.0.1:8000 and restarts it if it stops or stops answering.
# - Makes sure the Tailscale Funnel route https://modi.tail3f4d21.ts.net -> 127.0.0.1:8000 exists.
#   It only adds that route when Tailscale has no serve/funnel config at all; any other
#   existing route is left untouched and just logged.
# - Never stops a program it did not start itself.

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port = 8000
$Ts = 'C:\Program Files\Tailscale\tailscale.exe'
$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir 'watchdog.log'

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    try { Add-Content -Path $Log -Value $line -Encoding UTF8 } catch {}
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 2MB) {
        Move-Item -Force $Log ($Log + '.old')
    }
}

# Only one watchdog at a time (the 10-minute trigger just finds this one running and leaves).
$others = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'run_server_24_7\.ps1' }
if ($others) { exit 0 }

$Python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Python) { $Python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }

function Ensure-Funnel {
    if (-not (Test-Path $Ts)) { return }
    try {
        $status = (& $Ts funnel status 2>&1 | Out-String)
        if ($status -match "127\.0\.0\.1:$Port") { return }
        if ($status -match 'No serve config') {
            $out = (& $Ts funnel --bg $Port 2>&1 | Out-String).Trim()
            Write-Log "Funnel route added: $out"
        } elseif ($status -match 'proxy|path|text') {
            if (-not $script:funnelWarned) {
                Write-Log "Tailscale already has another serve/funnel route; left as it is: $($status.Trim())"
                $script:funnelWarned = $true
            }
        }
    } catch { Write-Log "Funnel check failed: $_" }
}

function Test-Health {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 "http://127.0.0.1:$Port/api/health"
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

Write-Log "Watchdog started (PID $PID)"
$server = $null
$badChecks = 0

while ($true) {
    Ensure-Funnel

    if ($server -and -not $server.HasExited) {
        if (Test-Health) { $badChecks = 0 } else { $badChecks++ }
        if ($badChecks -ge 3) {
            Write-Log "Server PID $($server.Id) not answering for 3 checks; restarting it"
            try { Stop-Process -Id $server.Id -Force -ErrorAction Stop } catch {}
            $server = $null
            $badChecks = 0
        }
        Start-Sleep -Seconds 30
        continue
    }
    if ($server) {
        Write-Log "Server PID $($server.Id) exited (code $($server.ExitCode)); restarting in 5 s"
        $server = $null
        Start-Sleep -Seconds 5
    }

    $listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listen) {
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($listen.OwningProcess)" -ErrorAction SilentlyContinue).CommandLine
        if ($cmd -notmatch 'server\.app:app') {
            Write-Log "Port $Port is used by another program (PID $($listen.OwningProcess)); not touching it, waiting"
        }
        Start-Sleep -Seconds 60
        continue
    }

    $server = Start-Process -FilePath $Python `
        -ArgumentList '-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', "$Port", '--log-level', 'warning' `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir 'server.out.log') `
        -RedirectStandardError (Join-Path $LogDir 'server.err.log')
    Write-Log "Server started (PID $($server.Id)) on 127.0.0.1:$Port"
    Start-Sleep -Seconds 10
}
