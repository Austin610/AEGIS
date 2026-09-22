$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDirectory
$pythonPath = Join-Path $projectDirectory '.venv/Scripts/python.exe'
foreach ($arguments in @(
    @('-m', 'pytest'),
    @('scripts/operational_drill.py'),
    @('-m', 'ruff', 'check', 'src', 'tests'),
    @('-m', 'ruff', 'format', '--check', 'src', 'tests'),
    @('-m', 'mypy'),
    @('-m', 'build')
)) {
    & $pythonPath @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& npm.cmd run check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& npm.cmd run test:browser
exit $LASTEXITCODE
