param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDir
$env:TEMP = Join-Path $projectDir '.cache\temp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $projectDir '.cache\pip'
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
& $Python -c "import sys; assert sys.version_info >= (3,13), 'Python 3.13 or later is required'"
if ($LASTEXITCODE -ne 0) { throw 'Select a Python 3.13+ executable with -Python.' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the project environment.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
Write-Host 'Ready. Configure your own model API key in .env, then run start.cmd.'
