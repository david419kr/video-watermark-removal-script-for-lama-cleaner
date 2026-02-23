param(
    [int]$InstanceCount = 1,
    [int]$BasePort = 8080
)

$ErrorActionPreference = "Stop"

if ($InstanceCount -lt 1) {
    Write-Error "InstanceCount must be >= 1"
    exit 1
}

$repoRoot = Split-Path -Path $PSScriptRoot -Parent
$batchScript = Join-Path $PSScriptRoot "batch.py"
$embeddedPython = Join-Path $repoRoot ".runtime\python310\python.exe"

if (Test-Path $embeddedPython) {
    $pythonExe = $embeddedPython
} else {
    $pythonExe = "python"
}

Push-Location $repoRoot
try {
    & $pythonExe $batchScript --instances $InstanceCount --base-port $BasePort
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
finally {
    Pop-Location
}
