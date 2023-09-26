@echo off
if not defined in_subprocess (cmd /k set in_subprocess=y ^& %0 %*) & exit

SET get_video_resolution=.\ffmpeg\bin\ffprobe.exe -v error -select_streams v:0 -show_entries "stream=width,height" -of "csv=s=x:p=0" .\video.mp4
SET get_mask_resolution=.\ffmpeg\bin\ffprobe.exe -v error -select_streams v:0 -show_entries "stream=width,height" -of "csv=s=x:p=0" .\mask.png
for /f "tokens=*" %%a IN ('%get_video_resolution%') do set video_resolution=%%a
for /f "tokens=*" %%b IN ('%get_mask_resolution%') do set mask_resolution=%%b

IF %video_resolution%==%mask_resolution% (
  START /wait /b powershell.exe -ExecutionPolicy Bypass -File .\scripts\01_audio_extract.ps1
  START /wait /b powershell.exe -ExecutionPolicy Bypass -File .\scripts\02_frame_extract.ps1
  START /wait /b powershell.exe -ExecutionPolicy Bypass -File .\scripts\03_frame_clean.ps1
  START /wait /b powershell.exe -ExecutionPolicy Bypass -File .\scripts\04_frame_merge.ps1
  START /wait /b powershell.exe -ExecutionPolicy Bypass -File .\scripts\05_audio_merge.ps1

  set /p YN="File saved as video_final.mp4, in output folder. cleanup temporary files? If not, you should manually delete temp folder before next task. (Y/N) "
  IF /i "%YN%" == "y" goto YES
  IF /i "%YN%" == "n" goto NO
  
  :YES
  START /wait /b powershell.exe -ExecutionPolicy Bypass -File .\scripts\06_cleanup.ps1

) ELSE (
  echo "video and mask resolution must be exact same"
)

:NO
pause