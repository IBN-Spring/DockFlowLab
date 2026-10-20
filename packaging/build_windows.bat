@echo off
setlocal enableextensions
cd /d "%~dp0\.."

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Activate the DockFlowLab conda environment first.
    exit /b 1
)

echo [1/3] Checking build dependencies...
python -c "import PyInstaller, PySide6, rdkit, meeko, openmm, pdbfixer" || (
    echo [ERROR] Missing build dependencies. Run:
    echo     pip install -e .[chem,build]
    exit /b 1
)

echo [2/3] Building with PyInstaller...
python -m PyInstaller --clean --noconfirm packaging\dockflowlab.spec || exit /b 1

echo [3/3] Creating release archive...
if not exist release mkdir release
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Compress-Archive -Path 'dist\DockFlowLab' -DestinationPath 'release\DockFlowLab-Windows-x64.zip' -CompressionLevel Optimal -Force"
if errorlevel 1 exit /b 1

echo.
echo Done: release\DockFlowLab-Windows-x64.zip
echo Test it by running dist\DockFlowLab\DockFlowLab.exe
endlocal
