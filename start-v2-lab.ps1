# Launch the isolated V2 lab bot from anywhere on Windows.
$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$runner = Join-Path $project 'scripts\v2_lab.py'
$venvPython = Join-Path $project 'venv\Scripts\python.exe'

if (-not (Test-Path $runner)) {
    Write-Error "Lab runner not found: $runner"
    exit 1
}

Set-Location $project
if (Test-Path $venvPython) {
    & $venvPython $runner
} else {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        Write-Error 'Python launcher not found. Install Python or set up the project venv.'
        exit 1
    }
    & py -3.13 $runner
}
exit $LASTEXITCODE
