$ffprobe = Join-Path $PSScriptRoot "..\ffmpeg\bin\ffprobe.exe"
$Rawfps = & $ffprobe -v error -select_streams v -of default=noprint_wrappers=1:nokey=1 -show_entries stream=avg_frame_rate (Join-Path $PSScriptRoot "..\video.mp4")
$Splitfps = $Rawfps.Split("/")
$Truefps = [Math]::Round([Math]::Floor(($Splitfps[0] / $Splitfps[1]) * 100) / 100, 2)

$ffmpeg = Join-Path $PSScriptRoot "..\ffmpeg\bin\ffmpeg.exe"
$inputPattern = Join-Path $PSScriptRoot "..\temp\output\%d.jpg"
$outputVideo = Join-Path $PSScriptRoot "..\temp\video_cleaned.mp4"
$hasNvenc = & $ffmpeg -hide_banner -encoders 2>$null | Select-String -Pattern "h264_nvenc" -Quiet

if ($hasNvenc) {
    Write-Host "Frame merge: NVENC path"
    # Keep quality close to previous libx264 -crf 7 while moving encode to GPU.
    & $ffmpeg -framerate $Truefps -i $inputPattern -c:v h264_nvenc -preset p5 -rc vbr -cq 7 -b:v 0 -pix_fmt yuv420p -y $outputVideo
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "NVENC merge failed. Falling back to libx264 path."
        & $ffmpeg -framerate $Truefps -i $inputPattern -c:v libx264 -crf 7 -pix_fmt yuv420p -y $outputVideo
    }
}
else {
    Write-Host "Frame merge: CPU libx264 path"
    & $ffmpeg -framerate $Truefps -i $inputPattern -c:v libx264 -crf 7 -pix_fmt yuv420p -y $outputVideo
}

if ($LASTEXITCODE -ne 0) {
    Write-Error "Frame merge failed."
    exit $LASTEXITCODE
}
