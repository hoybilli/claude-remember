# remember-probe (THROWAWAY; delete after use): Windows launcher for recorder.sh.
# Modelled on the port's scripts/run-hook.ps1: a bare `bash` under Windows
# PowerShell can resolve to WSL (System32 precedes Git on PATH), so Git Bash is
# located explicitly. Arguments are forwarded unchanged; stdin and stdout are
# not touched here -- bash inherits both handles, so the payload arrives byte
# for byte and the SessionStart envelope goes straight to the caller.
# The port's handle-detach step is left out: the recorder starts no background
# work. Every failure exits 0, like the real hooks.

function Write-ProbeMessage([string]$Message) {
    try { [Console]::Error.WriteLine($Message) }
    catch { Write-Error -ErrorAction Continue -Message $Message }
    # Also leave a trace beside the probe log (<plugin root>\..\probe-log.jsonl.err).
    try {
        $err = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'probe-log.jsonl.err'
        Add-Content -LiteralPath $err -Value ("{0} run-probe.ps1: {1}" -f (Get-Date -Format o), $Message)
    } catch { }
}

try {
    $ErrorActionPreference = 'Stop'
    $candidates = @()
    if ($env:REMEMBER_BASH) { $candidates += $env:REMEMBER_BASH }
    $bases = @($env:ProgramW6432, $env:ProgramFiles, ${env:ProgramFiles(x86)})
    if ($env:LOCALAPPDATA) { $bases += (Join-Path $env:LOCALAPPDATA 'Programs') }
    foreach ($base in $bases) {
        if ($base) { $candidates += (Join-Path $base 'Git\bin\bash.exe') }
    }
    $bash = $null
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c -PathType Leaf) { $bash = $c; break }
    }
    if (-not $bash) {
        $found = Get-Command bash.exe -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch 'System32' -and $_.Source -notmatch 'WindowsApps' } |
            Select-Object -First 1
        if ($found) { $bash = $found.Source }
    }
    if (-not $bash) {
        Write-ProbeMessage 'remember-probe: Git Bash not found; install Git for Windows or set REMEMBER_BASH'
        exit 0
    }

    $target = (Join-Path $PSScriptRoot 'recorder.sh').Replace('\', '/')
    & $bash $target @args
    exit 0
} catch {
    Write-ProbeMessage "remember-probe: launcher error: $_"
    exit 0
}
