$ErrorActionPreference = "Stop"

$ffmpeg = Join-Path $PSScriptRoot "..\ffmpeg\bin\ffmpeg.exe"
$video = Join-Path $PSScriptRoot "..\video.mp4"
$outputPattern = Join-Path $PSScriptRoot "..\temp\input\%d.jpg"

$hasCudaHwaccel = & $ffmpeg -hide_banner -hwaccels 2>$null | Select-String -Pattern "cuda" -Quiet

if ($hasCudaHwaccel) {
    Write-Host "Frame extract: CUDA decode path"
    & $ffmpeg -hwaccel cuda -i $video -q:v 1 $outputPattern
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "CUDA extract failed. Falling back to CPU path."
        & $ffmpeg -i $video -q:v 1 $outputPattern
    }
}
else {
    Write-Host "Frame extract: CPU path"
    & $ffmpeg -i $video -q:v 1 $outputPattern
}

if ($LASTEXITCODE -ne 0) {
    Write-Error "Frame extraction failed."
    exit $LASTEXITCODE
}
