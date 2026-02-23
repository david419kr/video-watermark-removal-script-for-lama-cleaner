param(
    [int]$BasePort = 8080,
    [int]$MaxProbe = 64
)

$count = 0

for ($i = 0; $i -lt $MaxProbe; $i++) {
    $port = $BasePort + $i
    $connection = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1

    if (-not $connection) {
        break
    }

    $owningProcessId = $connection.OwningProcess
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$owningProcessId" -ErrorAction SilentlyContinue

    if (-not $processInfo) {
        break
    }

    $commandLine = ("{0} {1}" -f $processInfo.Name, $processInfo.CommandLine).ToLowerInvariant()
    if ($commandLine -notmatch "lama-cleaner") {
        break
    }

    $count++
}

Write-Output $count
