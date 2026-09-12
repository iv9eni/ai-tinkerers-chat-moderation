$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$uv = Get-Command uv -ErrorAction SilentlyContinue | Select-Object -First 1

if ($uv) {
    $uvPath = $uv.Source
} else {
    $uvPath = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter uv.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}

if (-not $uvPath) {
    throw "uv.exe was not found. Restart PowerShell, or install uv with: winget install --id astral-sh.uv"
}

Push-Location $repo
try {
    & $uvPath run python scripts\token_dashboard.py
    Write-Host ""
    Write-Host "Dashboard:"
    Write-Host "$repo\reports\token_optimization.html"
} finally {
    Pop-Location
}
