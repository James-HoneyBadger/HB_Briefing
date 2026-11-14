<#
.SYNOPSIS
    Build the Windows portable zip and, when Inno Setup is installed, the installer.

.DESCRIPTION
    Thin wrapper around packaging\build.py so a Windows machine needs no bash. Run it from the
    repository root:  powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#>
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $root
try {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
    if (-not $python) { throw "Python 3.8+ is required on PATH." }

    & $python.Source packaging\build.py windows
    if ($LASTEXITCODE -ne 0) { throw "build.py exited with $LASTEXITCODE" }

    Write-Host ""
    Write-Host "Artefacts in dist\:"
    Get-ChildItem dist | Select-Object Name, @{n = 'MiB'; e = { [math]::Round($_.Length / 1MB, 1) } }
}
finally {
    Pop-Location
}
