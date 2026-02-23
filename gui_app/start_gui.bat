@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0\.."

set "GUI_DIR=%CD%\gui_app"
set "PYTHON_EXE=%CD%\.runtime\python310\python.exe"

if not exist "%PYTHON_EXE%" (
  set "PYTHON_EXE=python"
)

echo Using Python: %PYTHON_EXE%
"%PYTHON_EXE%" -m pip install -r "%GUI_DIR%\requirements.txt"
if errorlevel 1 (
  echo Failed to install GUI requirements.
  exit /b 1
)

"%PYTHON_EXE%" "%GUI_DIR%\main.py"
exit /b %errorlevel%
