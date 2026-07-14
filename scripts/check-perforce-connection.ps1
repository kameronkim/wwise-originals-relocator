<#
.SYNOPSIS
Checks the Perforce connection and Wwise project mapping without running or
rebuilding Wwise Originals Relocator.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\check-perforce-connection.ps1 -ProjectRoot "D:\Work\Dev\Ilias\Ilias_WwiseProject"

.NOTES
This script only runs read-only p4 commands: set, info, and where. It does not
open, edit, move, revert, or submit files. The generated report does not print
P4PASSWD or ticket contents.
#>
param(
    [Parameter(Position = 0)]
    [string]$ProjectRoot,
    [string]$P4Executable,
    [string]$P4Port,
    [string]$P4User,
    [string]$P4Client,
    [string]$P4Charset,
    [string]$OutputPath
)

# This script intentionally uses ASCII-only source text so it also runs under
# Windows PowerShell 5.1 without requiring a particular script-file encoding.
$ErrorActionPreference = "Stop"

function Resolve-P4Executable {
    param([string]$ConfiguredPath)

    if ($ConfiguredPath) {
        if (-not (Test-Path -LiteralPath $ConfiguredPath -PathType Leaf)) {
            throw "The configured p4 executable was not found: $ConfiguredPath"
        }
        return (Resolve-Path -LiteralPath $ConfiguredPath).Path
    }

    $command = Get-Command "p4.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) {
        $command = Get-Command "p4" -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    if ($command) {
        if ($command.Path) {
            return $command.Path
        }
        return $command.Source
    }

    $candidates = @(
        (Join-Path $env:ProgramFiles "Perforce\p4.exe")
    )
    if (${env:ProgramFiles(x86)}) {
        $candidates += Join-Path ${env:ProgramFiles(x86)} "Perforce\p4.exe"
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "p4.exe was not found in PATH or the standard Perforce install folders."
}

function Write-ReportLine {
    param([string]$Text = "")

    Write-Host $Text
    Add-Content -LiteralPath $script:ResolvedOutputPath -Value $Text -Encoding UTF8
}

function Format-CommandArgument {
    param([string]$Value)

    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

function Invoke-P4Diagnostic {
    param(
        [string]$Label,
        [string[]]$CommandArguments,
        [switch]$HideOutput
    )

    Write-ReportLine
    Write-ReportLine "[$Label]"
    $displayArguments = @($CommandArguments | ForEach-Object { Format-CommandArgument $_ })
    Write-ReportLine ("Command: {0} {1}" -f $script:ResolvedP4Executable, ($displayArguments -join " "))

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $nativeOutput = @(& $script:ResolvedP4Executable @CommandArguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    $lines = @($nativeOutput | ForEach-Object { $_.ToString() })
    if ($HideOutput) {
        Write-ReportLine "Output hidden; only the safe connection fields are shown below."
    }
    else {
        foreach ($line in $lines) {
            Write-ReportLine $line
        }
    }
    Write-ReportLine "Exit code: $exitCode"

    $text = $lines -join "`n"
    $reportedError = $text -match '(?im)^(error|fatal):|^\.\.\.\s+code\s+error|^\.\.\.\s+severity\s+[3-9]|connect to server failed|password.*invalid|not in client view'
    return [PSCustomObject]@{
        Label = $Label
        Arguments = $CommandArguments
        ExitCode = $exitCode
        Lines = $lines
        Text = $text
        Success = ($exitCode -eq 0 -and -not $reportedError)
    }
}

function Get-TaggedValue {
    param(
        [string]$Text,
        [string]$Name
    )

    $pattern = '(?m)^\.\.\.\s+' + [Regex]::Escape($Name) + '\s+(.+?)\s*$'
    $match = [Regex]::Match($Text, $pattern)
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }
    return $null
}

function Add-P4Option {
    param(
        [System.Collections.Generic.List[string]]$Target,
        [string]$Flag,
        [string]$Value
    )

    if ($Value) {
        $Target.Add($Flag)
        $Target.Add($Value)
    }
}

function Test-UsefulClientName {
    param([string]$Value)

    return $Value -and $Value -notmatch '^\*' -and $Value -ne 'unknown'
}

if (-not $ProjectRoot) {
    $ProjectRoot = Read-Host "Enter the Wwise project folder"
}
if (-not $ProjectRoot -or -not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "The Wwise project folder was not found: $ProjectRoot"
}

$ResolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$projectFiles = @(Get-ChildItem -LiteralPath $ResolvedProjectRoot -Filter "*.wproj" -File)
if ($projectFiles.Count -ne 1) {
    throw "The selected folder must contain exactly one .wproj file. Found: $($projectFiles.Count)"
}
$ProjectFile = $projectFiles[0].FullName
$ResolvedP4Executable = Resolve-P4Executable $P4Executable

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $PSScriptRoot "perforce-check-$timestamp.txt"
}
$outputParent = Split-Path -Parent $OutputPath
if ($outputParent -and -not (Test-Path -LiteralPath $outputParent)) {
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
}
$ResolvedOutputPath = [IO.Path]::GetFullPath($OutputPath)
Set-Content -LiteralPath $ResolvedOutputPath -Value "" -Encoding UTF8

Push-Location $ResolvedProjectRoot
try {
    Write-ReportLine "Wwise Originals Relocator - Perforce connection check"
    Write-ReportLine "Generated: $((Get-Date).ToString('o'))"
    Write-ReportLine "Project root: $ResolvedProjectRoot"
    Write-ReportLine "Project file: $ProjectFile"
    Write-ReportLine "p4 executable: $ResolvedP4Executable"
    Write-ReportLine "P4V running: $([bool](Get-Process -Name 'p4v' -ErrorAction SilentlyContinue))"
    Write-ReportLine
    Write-ReportLine "Process environment (no password or ticket is printed):"
    foreach ($name in @("P4PORT", "P4USER", "P4CLIENT", "P4CHARSET", "P4CONFIG")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        Write-ReportLine ("  {0}={1}" -f $name, $(if ($value) { $value } else { "<not set>" }))
    }

    $setResult = Invoke-P4Diagnostic -Label "Relevant p4 set values" -CommandArguments @("set") -HideOutput
    Write-ReportLine
    Write-ReportLine "Relevant values only:"
    $relevantSetLines = @($setResult.Lines | Where-Object { $_ -match '^(P4PORT|P4USER|P4CLIENT|P4CHARSET|P4CONFIG)=' })
    if ($relevantSetLines.Count -eq 0) {
        Write-ReportLine "  <none>"
    }
    else {
        foreach ($line in $relevantSetLines) {
            Write-ReportLine "  $line"
        }
    }

    $baseOptions = New-Object 'System.Collections.Generic.List[string]'
    Add-P4Option $baseOptions "-p" $P4Port
    Add-P4Option $baseOptions "-u" $P4User
    Add-P4Option $baseOptions "-c" $P4Client
    Add-P4Option $baseOptions "-C" $P4Charset

    $baseInfoArgs = @($baseOptions) + @("-ztag", "info")
    $baseInfo = Invoke-P4Diagnostic "A. Current Perforce context" $baseInfoArgs

    $serverAddress = Get-TaggedValue $baseInfo.Text "serverAddress"
    $userName = Get-TaggedValue $baseInfo.Text "userName"
    $clientName = Get-TaggedValue $baseInfo.Text "clientName"
    $serverVersion = Get-TaggedValue $baseInfo.Text "serverVersion"

    $baseWhereArgs = @($baseOptions) + @("where", $ProjectFile)
    $baseWhere = Invoke-P4Diagnostic "B. Project mapping in current context" $baseWhereArgs

    $appPort = $P4Port
    if (-not $appPort) { $appPort = $env:P4PORT }
    if (-not $appPort) { $appPort = $serverAddress }
    $appUser = $P4User
    if (-not $appUser) { $appUser = $env:P4USER }
    if (-not $appUser) { $appUser = $userName }
    $appClient = $P4Client
    if (-not $appClient) { $appClient = $env:P4CLIENT }
    if (-not $appClient -and (Test-UsefulClientName $clientName)) { $appClient = $clientName }
    $appCharset = $P4Charset
    if (-not $appCharset) { $appCharset = $env:P4CHARSET }

    $replayOptions = New-Object 'System.Collections.Generic.List[string]'
    Add-P4Option $replayOptions "-p" $appPort
    Add-P4Option $replayOptions "-u" $appUser
    Add-P4Option $replayOptions "-c" $appClient
    Add-P4Option $replayOptions "-C" $appCharset

    $replayInfo = $null
    $replayWhere = $null
    if ($baseInfo.Success -and $appPort) {
        $replayInfo = Invoke-P4Diagnostic "C. App-style replayed connection" (@($replayOptions) + @("-ztag", "info"))
        $replayWhere = Invoke-P4Diagnostic "D. Project mapping in replayed connection" (@($replayOptions) + @("where", $ProjectFile))
    }

    Write-ReportLine
    Write-ReportLine "[Summary]"
    Write-ReportLine "Current context connection: $(if ($baseInfo.Success) { 'PASS' } else { 'FAIL' })"
    Write-ReportLine "Current context project mapping: $(if ($baseWhere.Success) { 'PASS' } else { 'FAIL' })"
    if ($replayInfo) {
        Write-ReportLine "App-style replay connection: $(if ($replayInfo.Success) { 'PASS' } else { 'FAIL' })"
        Write-ReportLine "App-style replay project mapping: $(if ($replayWhere.Success) { 'PASS' } else { 'FAIL' })"
    }
    Write-ReportLine "Reported serverAddress: $(if ($serverAddress) { $serverAddress } else { '<not reported>' })"
    Write-ReportLine "Reported userName: $(if ($userName) { $userName } else { '<not reported>' })"
    Write-ReportLine "Reported clientName: $(if ($clientName) { $clientName } else { '<not reported>' })"
    Write-ReportLine "Reported serverVersion: $(if ($serverVersion) { $serverVersion } else { '<not reported>' })"

    Write-ReportLine
    if ($baseInfo.Success -and $replayInfo -and -not $replayInfo.Success) {
        Write-ReportLine "DIAGNOSIS: The existing Perforce context works, but replaying the address reported by p4 info fails."
        Write-ReportLine "This confirms the suspected app connection-detection issue."
    }
    elseif (-not $baseInfo.Success) {
        Write-ReportLine "DIAGNOSIS: The existing p4 context cannot connect. Check the P4V server, login, and P4PORT settings."
    }
    elseif (-not $baseWhere.Success) {
        Write-ReportLine "DIAGNOSIS: Perforce connects, but the selected Wwise project is not mapped in the current client view."
    }
    elseif ($replayInfo -and $replayInfo.Success -and $replayWhere.Success) {
        Write-ReportLine "DIAGNOSIS: Both the existing context and the app-style replay work. Perforce is ready for this project."
    }
    else {
        Write-ReportLine "DIAGNOSIS: Review the failed section above and share this report with the developer."
    }

    Write-ReportLine "Report: $ResolvedOutputPath"
}
finally {
    Pop-Location
}
