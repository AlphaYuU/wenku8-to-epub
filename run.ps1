$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$cacheRoot = Join-Path $projectRoot '.cache'
$tempRoot = Join-Path $projectRoot '.tmp'
$logRoot = Join-Path $projectRoot 'logs'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Project Python environment not found: $pythonExe"
}

$directories = @(
    (Join-Path $cacheRoot 'pip'),
    (Join-Path $cacheRoot 'pycache'),
    $tempRoot,
    $logRoot,
    (Join-Path $projectRoot 'epub')
)
foreach ($directory in $directories) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

$env:PIP_CACHE_DIR = Join-Path $cacheRoot 'pip'
$env:PYTHONPYCACHEPREFIX = Join-Path $cacheRoot 'pycache'
$env:PYTHONNOUSERSITE = '1'
$env:XDG_CACHE_HOME = $cacheRoot
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $cacheRoot 'ms-playwright'
$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$env:TMPDIR = $tempRoot

Push-Location -LiteralPath $projectRoot
$runStatus = 1
try {
    & $pythonExe (Join-Path $projectRoot 'main.py') @args
    $runStatus = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $runStatus
