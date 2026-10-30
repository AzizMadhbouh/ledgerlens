#requires -Version 5.1
param(
    [switch]$FullRefresh,
    [int]$Limit = 0,
    [string]$Model = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".venv\Scripts\ledgerlens.exe")) {
    throw "Environment not found. Run .\scripts\setup.ps1 first."
}

$arguments = @("run")
if ($FullRefresh) { $arguments += "--full-refresh" }
if ($Limit -gt 0) { $arguments += @("--limit", "$Limit") }
if ($Model) {
    # --model is a global option, so place it before the subcommand.
    $arguments = @("--model", $Model) + $arguments
}

& .venv\Scripts\ledgerlens.exe @arguments
exit $LASTEXITCODE
