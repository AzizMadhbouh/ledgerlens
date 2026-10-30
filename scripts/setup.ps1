#requires -Version 5.1
<#
.SYNOPSIS
  Install LedgerLens' local prerequisites and Python environment on Windows.

.DESCRIPTION
  Installs Tesseract OCR and Ollama with winget, then creates .venv, installs
  the Python package and pulls the local model. Ollama models are a few GB;
  check free disk space before running.
#>
param(
    [string]$Model = $(if ($env:LEDGERLENS_MODEL) { $env:LEDGERLENS_MODEL } else { "llama3.2:3b" }),
    [switch]$SkipSystemPackages
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "`nLedgerLens setup" -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot"
Write-Host "Model:   $Model"

if (-not $SkipSystemPackages) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget is required. Install App Installer from the Microsoft Store."
    }

    Write-Host "`n[1/5] Tesseract OCR" -ForegroundColor Yellow
    winget install --id UB-Mannheim.TesseractOCR --exact --silent `
        --accept-package-agreements --accept-source-agreements

    Write-Host "`n[2/5] Ollama" -ForegroundColor Yellow
    winget install --id Ollama.Ollama --exact --silent `
        --accept-package-agreements --accept-source-agreements
} else {
    Write-Host "`n[1/5] System packages skipped."
}

$Tesseract = Join-Path $env:ProgramFiles "Tesseract-OCR\tesseract.exe"
if (-not (Test-Path $Tesseract)) {
    $onPath = Get-Command tesseract -ErrorAction SilentlyContinue
    if (-not $onPath) {
        throw "Tesseract was not found. Install it or set TESSERACT_CMD in .env."
    }
}
Write-Host "Tesseract: $Tesseract"

Write-Host "`n[3/5] Python environment" -ForegroundColor Yellow
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}
& .venv\Scripts\python.exe -m pip install --upgrade pip
& .venv\Scripts\python.exe -m pip install -e ".[dev]"

$Ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $Ollama) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $candidate) { $Ollama = $candidate }
}
if (-not $Ollama) {
    throw "Ollama was installed but ollama.exe was not found. Open Ollama once, then rerun setup."
}

Write-Host "`n[4/5] Ollama service" -ForegroundColor Yellow
$healthy = $false
try {
    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3
    $healthy = $true
} catch {
    Write-Host "Starting Ollama..."
    Start-Process -FilePath $Ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 750
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
            $healthy = $true
            break
        } catch { }
    }
}
if (-not $healthy) {
    throw "Ollama did not become ready. Start Ollama from the Start menu and rerun setup."
}

Write-Host "`n[5/5] Model" -ForegroundColor Yellow
& $Ollama pull $Model

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "Run: .\scripts\run.ps1"
Write-Host "Or:  .venv\Scripts\ledgerlens.exe run"
