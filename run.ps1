param([Parameter(ValueFromRemainingArguments=$true)][string[]]$ToolArguments)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11 or newer required'"
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11+ and enable Add to PATH first.' }
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
}
& '.\.venv\Scripts\python.exe' -c "import spark_recovery, pycdlib, zstandard" 2>$null
if ($LASTEXITCODE -ne 0) {
    & '.\.venv\Scripts\python.exe' -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; rerun to retry.' }
}
& '.\.venv\Scripts\python.exe' -m spark_recovery @ToolArguments
exit $LASTEXITCODE
