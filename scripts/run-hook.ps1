# Windows launcher for claude-remember hooks under VS Code Agents / Copilot CLI.
# Copilot runs a hook's `powershell` command through Windows PowerShell, where a
# bare `bash` may resolve to WSL (System32 precedes Git on PATH). This locates
# Git Bash explicitly, forwards stdin (the hook payload JSON) and stdout
# untouched as UTF-8, and returns bash's exit code.
#
# Every failure of the launcher itself exits 0, like every hook path: a hook
# that cannot run does nothing rather than failing the session. Only bash's own
# status is forwarded, and the hook scripts exit 0.
param(
    [Parameter(Mandatory = $true)][string]$Script,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)
try {
    $ErrorActionPreference = 'Stop'
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [Console]::InputEncoding = $utf8
    [Console]::OutputEncoding = $utf8
    $global:OutputEncoding = $utf8

    $candidates = @()
    if ($env:REMEMBER_BASH) { $candidates += $env:REMEMBER_BASH }
    $bases = @($env:ProgramFiles, ${env:ProgramFiles(x86)})
    if ($env:LOCALAPPDATA) { $bases += (Join-Path $env:LOCALAPPDATA 'Programs') }
    foreach ($base in $bases) {
        if ($base) { $candidates += (Join-Path $base 'Git\bin\bash.exe') }
    }
    $bash = $null
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) { $bash = $c; break }
    }
    if (-not $bash) {
        # Skip WSL's launchers: System32\bash.exe and the Store alias under
        # ...\WindowsApps\.
        $found = Get-Command bash.exe -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch 'System32' -and $_.Source -notmatch 'WindowsApps' } |
            Select-Object -First 1
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

    # The hooks return at once and leave their work to a detached background
    # child; the caller waits for this process's stdout to reach EOF. Windows
    # PowerShell 5.1 holds an extra inheritable duplicate of its stdout handle,
    # and everything it starts -- bash, then every child bash starts -- inherits
    # every inheritable handle, so that child would keep the caller's pipe open
    # until it finished. Clear the inherit flag on every handle but the three
    # standard ones (which bash receives as its own stdio and does not pass to a
    # child whose fds it redirected). Best effort: if this step fails (e.g.
    # Add-Type cannot compile), the hook still runs and the caller just waits
    # for the background work, as it did before.
    try {
        if (-not ('ClaudeRemember.Handles' -as [type])) {
            Add-Type -Namespace ClaudeRemember -Name Handles -MemberDefinition @'
[DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int n);
[DllImport("kernel32.dll")] static extern bool GetHandleInformation(IntPtr h, out uint flags);
[DllImport("kernel32.dll")] static extern bool SetHandleInformation(IntPtr h, uint mask, uint flags);
public static void KeepOnlyStdioInheritable() {
    long i = GetStdHandle(-10).ToInt64(), o = GetStdHandle(-11).ToInt64(), e = GetStdHandle(-12).ToInt64();
    for (long v = 4; v < 0x100000; v += 4) {
        if (v == i || v == o || v == e) continue;
        uint flags; IntPtr h = new IntPtr(v);
        if (GetHandleInformation(h, out flags) && (flags & 1) != 0) SetHandleInformation(h, 1, 0);
    }
}
'@
        }
        [ClaudeRemember.Handles]::KeepOnlyStdioInheritable()
    } catch { }

    $payload = [Console]::In.ReadToEnd()
    $payload | & $bash $target @Rest
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine("claude-remember: launcher error: $_")
    exit 0
}
