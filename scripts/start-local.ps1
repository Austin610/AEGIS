param([int]$Port = 8766)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectDirectory '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create the virtual environment and install the package first; see README.md.'
}
Set-Location -LiteralPath $projectDirectory
if (-not (Test-Path Env:AEGIS_DURABLE_JOBS)) {
    $env:AEGIS_DURABLE_JOBS = '1'
}
Write-Host "AEGIS will be available at http://127.0.0.1:$Port"
Write-Host 'Sign in using the token stored in .aegis/app/access-token.'
& $pythonPath -m aegis serve --port $Port --data-dir .aegis/app
exit $LASTEXITCODE
