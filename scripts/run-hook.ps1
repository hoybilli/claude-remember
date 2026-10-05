# Windows launcher for claude-remember hooks under VS Code Agents / Copilot CLI.
# Copilot runs a hook's `powershell` command through Windows PowerShell, where a
# bare `bash` may resolve to WSL (System32 precedes Git on PATH). This locates
# Git Bash explicitly, hands it this process's own stdin (the hook payload
# JSON) and stdout, and returns bash's exit code.
#
# Once the launcher is running, every failure of its own exits 0, like every
# hook path: a hook that cannot run does nothing rather than failing the
# session. Only bash's own status is forwarded, and the hook scripts exit 0.
# The one exception is before any of this runs: under -NonInteractive a call
# without the script name fails parameter binding with exit 1 (the manifest
# always passes the name).
param(
    [Parameter(Mandatory = $true)][string]$Script,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)

# One line on stderr. [Console]::Error is a method call on a non-core type,
# which Constrained Language Mode refuses; Write-Error is allowed there.
# (Write-Warning is not used: Windows PowerShell 5.1 writes the warning stream
# to stdout, which would land in front of a SessionStart hook's JSON.)
function Write-LauncherMessage([string]$Message) {
    try { [Console]::Error.WriteLine($Message) }
    catch { Write-Error -ErrorAction Continue -Message $Message }
}

try {
    $ErrorActionPreference = 'Stop'

    # Everything from here to the language-mode check uses cmdlets and core
    # types only, so it runs under Constrained Language Mode too.
    $candidates = @()
    if ($env:REMEMBER_BASH) { $candidates += $env:REMEMBER_BASH }
    # ProgramW6432 first: a 32-bit PowerShell on 64-bit Windows resolves
    # ProgramFiles to "Program Files (x86)" and would miss a 64-bit Git.
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
        # Skip WSL's launchers: System32\bash.exe and the Store alias under
        # ...\WindowsApps\.
        $found = Get-Command bash.exe -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch 'System32' -and $_.Source -notmatch 'WindowsApps' } |
            Select-Object -First 1
        if ($found) { $bash = $found.Source }
    }
    if (-not $bash) {
        Write-LauncherMessage 'claude-remember: Git Bash not found; install Git for Windows or set REMEMBER_BASH to bash.exe'
        exit 0
    }

    # The hooks sit beside this launcher, in <plugin root>/scripts.
    $target = (Join-Path $PSScriptRoot $Script).Replace('\', '/')

    # stdin is not read here: bash inherits this process's stdin handle and
    # reads the payload itself, so the bytes arrive exactly as the caller wrote
    # them (no re-encoding, no appended CRLF), EOF is the caller's own, and the
    # hooks' own bounded `read -t 1` applies when a caller leaves the pipe open.
    # Likewise stdout: bash writes to the inherited handle directly.

    if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {
        # Constrained Language Mode (AppLocker / WDAC script enforcement):
        # Add-Type below is refused, so the background work cannot be
        # detached and the caller waits for it. The hook itself still runs.
        Write-LauncherMessage 'claude-remember: launcher: Constrained Language Mode; could not detach background work, the caller will wait for it'
        & $bash $target @Rest
        exit $LASTEXITCODE
    }

    # The launcher's own stderr lines carry paths and localized exception text;
    # emit them as UTF-8 rather than the console's OEM code page. Not set under
    # Constrained Language Mode above (the call is refused there).
    try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

    # The hooks return at once and leave their work to a detached background
    # child; the caller waits for this process's stdout to reach EOF. Windows
    # PowerShell 5.1 holds an extra inheritable duplicate of its stdout handle,
    # and everything it starts -- bash, then every child bash starts -- inherits
    # every inheritable handle, so that child would keep the caller's pipe open
    # until it finished. Clear the inherit flag on every handle but the three
    # standard ones (which bash receives as its own stdio and does not pass to a
    # child whose fds it redirected). Best effort: if this step fails (e.g.
    # Add-Type cannot compile), one line goes to stderr, the hook still runs and
    # the caller just waits for the background work, as it did before.
    try {
        # The type is never already loaded under the -File entry (a fresh
        # process per hook); only an in-process reuse of the launcher (and the
        # fallback test) finds it loaded and skips Add-Type.
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
    } catch {
        Write-LauncherMessage "claude-remember: launcher: could not detach background work ($_); the caller will wait for it"
    }

    & $bash $target @Rest
    exit $LASTEXITCODE
} catch {
    Write-LauncherMessage "claude-remember: launcher error: $_"
    exit 0
}
