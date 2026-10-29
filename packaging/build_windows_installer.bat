@echo off
setlocal enableextensions
cd /d "%~dp0\.."

if not exist "dist\DockFlowLab\DockFlowLab.exe" (
    echo [ERROR] dist\DockFlowLab\DockFlowLab.exe not found.
    echo         Run packaging\build_windows.bat first.
    exit /b 1
)

set "MAKENSIS=%~1"
if not defined MAKENSIS set "MAKENSIS=%MAKENSIS_EXE%"
if not defined MAKENSIS set "MAKENSIS=makensis"

if exist "%MAKENSIS%" goto have_makensis
where "%MAKENSIS%" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] makensis.exe not found.
    echo         Install NSIS 3, or pass its path:
    echo         packaging\build_windows_installer.bat "C:\path\to\makensis.exe"
    exit /b 1
)

:have_makensis
if not exist release mkdir release

echo [1/2] Checking portable build...
echo [2/2] Building Windows installer...
"%MAKENSIS%" /V2 packaging\installer.nsi
if errorlevel 1 exit /b 1

if not exist "release\DockFlowLab-Setup-2.5.0-x64.exe" (
    echo [ERROR] Installer was not created.
    exit /b 1
)

echo.
echo Done: release\DockFlowLab-Setup-2.5.0-x64.exe
endlocal
