@echo off
if not defined in_subprocess (cmd /k set in_subprocess=y ^& %0 %*) & exit
setlocal EnableExtensions EnableDelayedExpansion

SET get_video_resolution=.\ffmpeg\bin\ffprobe.exe -v error -select_streams v:0 -show_entries "stream=width,height" -of "csv=s=x:p=0" .\video.mp4
SET get_mask_resolution=.\ffmpeg\bin\ffprobe.exe -v error -select_streams v:0 -show_entries "stream=width,height" -of "csv=s=x:p=0" .\mask.png
for /f "tokens=*" %%a IN ('%get_video_resolution%') do set video_resolution=%%a
for /f "tokens=*" %%b IN ('%get_mask_resolution%') do set mask_resolution=%%b
set "LAMA_BASE_PORT=8080"

IF "%video_resolution%"=="%mask_resolution%" (
  START /wait /b powershell.exe -ExecutionPolicy Bypass -File .\scripts\01_audio_extract.ps1
  START /wait /b powershell.exe -ExecutionPolicy Bypass -File .\scripts\02_frame_extract.ps1

  set "lama_instance_count="
  for /f "tokens=*" %%c IN ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\detect_lama_instances.ps1 -BasePort %LAMA_BASE_PORT%') do set "lama_instance_count=%%c"

  if not defined lama_instance_count (
    echo Failed to detect running lama-cleaner instances.
    echo Please run .\run-lama-cleaner.bat first.
    goto NO
  )

  for /f "delims=0123456789" %%d IN ("!lama_instance_count!") do (
    echo Invalid lama-cleaner instance count detected: "!lama_instance_count!"
    goto NO
  )

  IF !lama_instance_count! LSS 1 (
    echo No running lama-cleaner instance detected from port %LAMA_BASE_PORT%.
    echo Please run .\run-lama-cleaner.bat first.
    goto NO
  )

  echo Detected !lama_instance_count! lama-cleaner instance^(s^) from port %LAMA_BASE_PORT%.
  START /wait /b powershell.exe -ExecutionPolicy Bypass -File .\scripts\03_frame_clean.ps1 -InstanceCount !lama_instance_count! -BasePort %LAMA_BASE_PORT%
  IF errorlevel 1 goto NO

  START /wait /b powershell.exe -ExecutionPolicy Bypass -File .\scripts\04_frame_merge.ps1
  START /wait /b powershell.exe -ExecutionPolicy Bypass -File .\scripts\05_audio_merge.ps1

  set /p YN="File saved as video_final.mp4, in output folder. cleanup temporary files? If not, you should manually delete temp folder before next task. (Y/N) "
  IF /i "!YN!" == "y" goto YES
  IF /i "!YN!" == "n" goto NO
  
  :YES
  START /wait /b powershell.exe -ExecutionPolicy Bypass -File .\scripts\06_cleanup.ps1

) ELSE (
  echo "video and mask resolution must be exact same"
)

:NO
pause
