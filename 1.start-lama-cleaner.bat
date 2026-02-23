@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "RUNTIME_DIR=%CD%\.runtime"
set "PY_VERSION=3.10.11"
set "PY_DIR=%RUNTIME_DIR%\python310"
set "PY_ZIP=%RUNTIME_DIR%\python-%PY_VERSION%-embed-amd64.zip"
set "PY_URL=https://www.python.org/ftp/python/%PY_VERSION%/python-%PY_VERSION%-embed-amd64.zip"
set "PYTHON_EXE=%PY_DIR%\python.exe"
set "PIP_EXE=%PY_DIR%\Scripts\pip.exe"
set "LAMA_EXE=%PY_DIR%\Scripts\lama-cleaner.exe"
set "PYTHON_PTH=%PY_DIR%\python310._pth"
set "GET_PIP=%RUNTIME_DIR%\get-pip.py"
set "GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py"
set "GET_PIP_URL_FALLBACK=https://bootstrap.pypa.io/pip/get-pip.py"
set "INSTALL_MARKER=%RUNTIME_DIR%\lama_cleaner_ready.flag"
set "BASE_PORT=8080"

if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"

call :ensure_python
if errorlevel 1 goto :fail

call :ensure_packages
if errorlevel 1 goto :fail

echo.
set "INSTANCE_COUNT="
set /p "INSTANCE_COUNT=How many lama-cleaner instances to run? [default: 1] "
set "INSTANCE_COUNT=%INSTANCE_COUNT: =%"
if not defined INSTANCE_COUNT set "INSTANCE_COUNT=1"

for /f "delims=0123456789" %%A in ("%INSTANCE_COUNT%") do (
  echo Invalid input: "%INSTANCE_COUNT%"
  echo Please enter a positive integer.
  goto :fail
)

if "%INSTANCE_COUNT%"=="0" (
  echo Invalid input: "%INSTANCE_COUNT%"
  echo Please enter a positive integer.
  goto :fail
)

if "%INSTANCE_COUNT%"=="1" (
  echo Starting lama-cleaner on port %BASE_PORT%...
  "%LAMA_EXE%" --model=lama --device=cuda --port=%BASE_PORT%
  goto :eof
)

set /a END_INDEX=%INSTANCE_COUNT%-1
echo Starting %INSTANCE_COUNT% lama-cleaner instances from port %BASE_PORT%...
for /L %%I in (0,1,!END_INDEX!) do (
  set /a PORT=%BASE_PORT%+%%I
  echo Launching lama-cleaner on port !PORT!...
  start "lama-cleaner-!PORT!" "%LAMA_EXE%" --model=lama --device=cuda --port=!PORT!
)
echo All instances launched.
goto :eof

:ensure_python
if exist "%PYTHON_EXE%" (
  echo Local Python already exists: "%PYTHON_EXE%"
) else (
  echo Downloading Python %PY_VERSION% embeddable package...
  call :download "%PY_URL%" "%PY_ZIP%"
  if errorlevel 1 exit /b 1

  if exist "%PY_DIR%" rmdir /s /q "%PY_DIR%"
  mkdir "%PY_DIR%"

  echo Extracting Python...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%PY_ZIP%' -DestinationPath '%PY_DIR%' -Force"
  if errorlevel 1 (
    echo Failed to extract Python package.
    exit /b 1
  )
)

if not exist "%PYTHON_PTH%" (
  echo Cannot find "%PYTHON_PTH%".
  exit /b 1
)

echo Configuring embeddable Python site-packages...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$pth='%PYTHON_PTH%'; $lines=Get-Content -LiteralPath $pth; $found=$false; for($i=0;$i -lt $lines.Count;$i++){ if($lines[$i] -match '^\s*#\s*import site\s*$'){ $lines[$i]='import site'; $found=$true } elseif($lines[$i] -match '^\s*import site\s*$'){ $found=$true } }; if(-not $found){ $lines += 'import site' }; Set-Content -LiteralPath $pth -Value $lines -Encoding ascii"
if errorlevel 1 (
  echo Failed to update python310._pth.
  exit /b 1
)

if exist "%PIP_EXE%" (
  echo pip already exists.
  exit /b 0
)

echo Installing pip...
call :download "%GET_PIP_URL%" "%GET_PIP%"
if errorlevel 1 (
  echo Primary get-pip URL failed. Trying fallback URL...
  call :download "%GET_PIP_URL_FALLBACK%" "%GET_PIP%"
  if errorlevel 1 exit /b 1
)

"%PYTHON_EXE%" "%GET_PIP%"
if errorlevel 1 (
  echo Failed to install pip.
  exit /b 1
)

if not exist "%PIP_EXE%" (
  echo pip installation completed but pip.exe was not found.
  exit /b 1
)

exit /b 0

:ensure_packages
if exist "%INSTALL_MARKER%" (
  echo Found existing install marker. Verifying package versions...
  call :verify_install
  if not errorlevel 1 (
    echo Environment verification passed. Skipping installation.
    exit /b 0
  )
  echo Installed environment is incomplete or mismatched. Reinstalling packages...
  del /f /q "%INSTALL_MARKER%" >nul 2>&1
)

echo Installing required Python packages...
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 (
  echo Failed to upgrade pip.
  exit /b 1
)

"%PYTHON_EXE%" -m pip install torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 (
  echo Failed to install CUDA torch packages.
  exit /b 1
)

"%PYTHON_EXE%" -m pip install lama-cleaner
if errorlevel 1 (
  echo Failed to install lama-cleaner.
  exit /b 1
)

"%PYTHON_EXE%" -m pip install huggingface_hub==0.14.1
if errorlevel 1 (
  echo Failed to install huggingface_hub==0.14.1.
  exit /b 1
)

call :verify_install
if errorlevel 1 (
  echo Post-install verification failed.
  exit /b 1
)

echo Installed on %date% %time%> "%INSTALL_MARKER%"
echo Package installation and verification completed.
exit /b 0

:verify_install
"%PYTHON_EXE%" -c "import os,sys,torch,torchvision,torchaudio,huggingface_hub,lama_cleaner; assert torch.__version__.startswith('2.10.0'), torch.__version__; assert torchvision.__version__.startswith('0.25.0'), torchvision.__version__; assert torchaudio.__version__.startswith('2.10.0'), torchaudio.__version__; assert huggingface_hub.__version__ == '0.14.1', huggingface_hub.__version__; assert os.path.exists(os.path.join(os.path.dirname(sys.executable),'Scripts','lama-cleaner.exe'))"
exit /b %errorlevel%

:download
set "URL=%~1"
set "DEST=%~2"
echo Downloading "!URL!"...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '!URL!' -OutFile '!DEST!' -UseBasicParsing"
if errorlevel 1 (
  echo Failed to download "!URL!".
  exit /b 1
)
exit /b 0

:fail
echo.
echo Setup failed.
exit /b 1
