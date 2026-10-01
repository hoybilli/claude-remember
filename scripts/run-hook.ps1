# Windows launcher for claude-remember hooks under VS Code Agents / Copilot CLI.
# Copilot runs a hook's `powershell` command through Windows PowerShell, where a
# bare `bash` may resolve to WSL (System32 precedes Git on PATH). This locates
# Git Bash explicitly, forwards stdin (the hook payload JSON) and stdout
# untouched as UTF-8, and returns bash's exit code.
param(
    [Parameter(Mandatory = $true)][string]$Script,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$global:OutputEncoding = $utf8

$candidates = @()
if ($env:REMEMBER_BASH) { $candidates += $env:REMEMBER_BASH }
foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
    if ($base) { $candidates += (Join-Path $base 'Git\bin\bash.exe') }
}
$bash = $null
foreach ($c in $candidates) {
    if (Test-Path -LiteralPath $c) { $bash = $c; break }
}
if (-not $bash) {
    $found = Get-Command bash.exe -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch 'System32' } | Select-Object -First 1
    if ($found) { $bash = $found.Source }
}
if (-not $bash) {
    [Console]::Error.WriteLine('claude-remember: Git Bash not found; install Git for Windows or set REMEMBER_BASH to bash.exe')
    exit 0
}

$root = $env:CLAUDE_PLUGIN_ROOT
if (-not $root) { $root = $env:COPILOT_PLUGIN_ROOT }
if (-not $root) { $root = Split-Path -Parent $PSScriptRoot }
$target = (Join-Path (Join-Path $root 'scripts') $Script).Replace('\', '/')

$payload = [Console]::In.ReadToEnd()
$payload | & $bash $target @Rest
exit $LASTEXITCODE
