@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "ROOT=%CD%"
set "RUNTIME_DIR=%ROOT%\.runtime"
set "LOCAL_PY_DIR=%RUNTIME_DIR%\python310"
set "PYTHON_EXE=%LOCAL_PY_DIR%\python.exe"
set "DOWNLOAD_DIR=%RUNTIME_DIR%\downloads"

set "PYTHON_VERSION=3.10.11"
set "PYTHON_EMBED_ZIP=python-%PYTHON_VERSION%-embed-amd64.zip"
set "PYTHON_EMBED_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/%PYTHON_EMBED_ZIP%"
set "PYTHON_ZIP_PATH=%DOWNLOAD_DIR%\%PYTHON_EMBED_ZIP%"

set "GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py"
set "GET_PIP_PATH=%DOWNLOAD_DIR%\get-pip.py"

set "TORCH_VERSION=2.10.0"
set "TORCHVISION_VERSION=0.25.0"
set "TORCHAUDIO_VERSION=2.10.0"
set "TORCH_CUDA_PREFIX=12.8"
set "LAMA_CLEANER_VERSION=1.2.5"
set "HF_HUB_VERSION=0.14.1"

call :ensure_embedded_python
if errorlevel 1 goto :setup_fail

echo Using Python: %PYTHON_EXE%
call :ensure_pip
if errorlevel 1 goto :setup_fail

set "PY_SCRIPTS=%LOCAL_PY_DIR%\Scripts"
if exist "%PY_SCRIPTS%" set "PATH=%PY_SCRIPTS%;%PATH%"

echo Installing GUI requirements...
"%PYTHON_EXE%" -m pip install -r "%ROOT%\requirements.txt"
if errorlevel 1 (
  echo Failed to install GUI requirements.
  goto :setup_fail
)

call :ensure_lama_cleaner
if errorlevel 1 goto :setup_fail

call :verify_lama_cleaner
if errorlevel 1 goto :setup_fail

"%PYTHON_EXE%" "%ROOT%\main.py"
exit /b %errorlevel%

:ensure_embedded_python
if exist "%PYTHON_EXE%" exit /b 0

echo Embedded Python not found. Preparing Python %PYTHON_VERSION%...
if not exist "%DOWNLOAD_DIR%" mkdir "%DOWNLOAD_DIR%"

call :download_file "%PYTHON_EMBED_URL%" "%PYTHON_ZIP_PATH%"
if errorlevel 1 exit /b 1

if exist "%LOCAL_PY_DIR%" rmdir /s /q "%LOCAL_PY_DIR%"
mkdir "%LOCAL_PY_DIR%"

echo Extracting embedded Python...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%PYTHON_ZIP_PATH%' -DestinationPath '%LOCAL_PY_DIR%' -Force"
if errorlevel 1 (
  echo Failed to extract embedded Python archive.
  exit /b 1
)

call :configure_embedded_python
if errorlevel 1 exit /b 1

if not exist "%PYTHON_EXE%" (
  echo Embedded Python executable not found after extraction.
  exit /b 1
)

exit /b 0

:configure_embedded_python
echo Configuring embedded Python site-packages...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$pth = Get-ChildItem -LiteralPath '%LOCAL_PY_DIR%' -Filter 'python*._pth' | Select-Object -First 1; if (-not $pth) { exit 1 }; $lines = Get-Content -LiteralPath $pth.FullName; $out = @(); foreach ($line in $lines) { if ($line -match '^\s*#\s*import site') { $out += 'import site' } else { $out += $line } }; if (-not ($out -contains 'import site')) { $out += 'import site' }; if (-not ($out -contains 'Lib\site-packages')) { $out += 'Lib\site-packages' }; Set-Content -LiteralPath $pth.FullName -Value $out -Encoding ASCII"
if errorlevel 1 (
  echo Failed to configure embedded Python path settings.
  exit /b 1
)
exit /b 0

:ensure_pip
"%PYTHON_EXE%" -m pip --version >nul 2>&1
if not errorlevel 1 exit /b 0

echo pip is missing. Bootstrapping pip...
if not exist "%DOWNLOAD_DIR%" mkdir "%DOWNLOAD_DIR%"
call :download_file "%GET_PIP_URL%" "%GET_PIP_PATH%"
if errorlevel 1 exit /b 1

"%PYTHON_EXE%" "%GET_PIP_PATH%" --disable-pip-version-check
"%PYTHON_EXE%" -m pip --version >nul 2>&1
if not errorlevel 1 exit /b 0

echo Failed to initialize pip.
exit /b 1

:ensure_lama_cleaner
echo Checking lama-cleaner runtime requirements...
call :is_lama_ready
if not errorlevel 1 (
  echo lama-cleaner environment looks ready. Skipping install.
  exit /b 0
)

echo Installing CUDA torch packages...
"%PYTHON_EXE%" -m pip install --no-cache-dir --force-reinstall torch==%TORCH_VERSION% torchvision==%TORCHVISION_VERSION% torchaudio==%TORCHAUDIO_VERSION% --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 (
  echo Failed to install torch CUDA packages.
  exit /b 1
)

echo Installing lama-cleaner %LAMA_CLEANER_VERSION%...
"%PYTHON_EXE%" -m pip install --no-cache-dir --force-reinstall lama-cleaner==%LAMA_CLEANER_VERSION%
if errorlevel 1 (
  echo Failed to install lama-cleaner.
  exit /b 1
)

echo Pinning huggingface_hub==%HF_HUB_VERSION% for lama-cleaner compatibility...
"%PYTHON_EXE%" -m pip install --no-cache-dir --force-reinstall --no-deps huggingface_hub==%HF_HUB_VERSION%
if errorlevel 1 (
  echo Failed to install huggingface_hub.
  exit /b 1
)

call :is_lama_ready
if errorlevel 1 (
  echo lama-cleaner dependency validation failed after install.
  call :print_lama_validation_details
  exit /b 1
)

exit /b 0

:is_lama_ready
"%PYTHON_EXE%" -c "import importlib.metadata as m, importlib.util as u, sys, torch; req=[('torch','%TORCH_VERSION%'),('torchvision','%TORCHVISION_VERSION%'),('torchaudio','%TORCHAUDIO_VERSION%'),('huggingface_hub','%HF_HUB_VERSION%'),('lama-cleaner','%LAMA_CLEANER_VERSION%')]; ok=(u.find_spec('lama_cleaner') is not None) and all(m.version(p).startswith(v) for p,v in req); cuda=(torch.version.cuda or ''); tver=getattr(torch,'__version__',''); ok=ok and (cuda.startswith('%TORCH_CUDA_PREFIX%') or ('+cu128' in tver)); sys.exit(0 if ok else 1)" >nul 2>&1
if errorlevel 1 exit /b 1

exit /b 0

:verify_lama_cleaner
echo Verifying lama-cleaner...
call :is_lama_ready
if errorlevel 1 (
  echo lama-cleaner verification failed.
  call :print_lama_validation_details
  exit /b 1
)

"%PYTHON_EXE%" -c "import sys; from lama_cleaner import entry_point; sys.exit(0 if callable(entry_point) else 1)" >nul 2>&1
if errorlevel 1 (
  echo lama-cleaner module command check failed.
  call :print_lama_validation_details
  exit /b 1
)
echo lama-cleaner verification completed.
exit /b 0

:print_lama_validation_details
echo Validation details:
"%PYTHON_EXE%" -m pip show torch torchvision torchaudio lama-cleaner huggingface_hub
"%PYTHON_EXE%" -c "import torch; print('torch.__version__=' + str(torch.__version__)); print('torch.version.cuda=' + str(torch.version.cuda))"
"%PYTHON_EXE%" -c "import sys; from lama_cleaner import entry_point; sys.argv=['lama-cleaner','--help']; entry_point()" >nul 2>&1
if errorlevel 1 (
  echo lama-cleaner entry-point check: FAILED
) else (
  echo lama-cleaner entry-point check: OK
)
exit /b 0

:download_file
set "URL=%~1"
set "OUT=%~2"
echo Downloading "%URL%"...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%URL%' -OutFile '%OUT%'"
if errorlevel 1 (
  echo Failed to download "%URL%".
  exit /b 1
)
if not exist "%OUT%" (
  echo Download output missing: "%OUT%"
  exit /b 1
)
exit /b 0

:setup_fail
echo Setup failed.
exit /b 1
