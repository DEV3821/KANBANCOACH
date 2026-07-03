@echo off
cd /d "%~dp0.."
echo Installing SAMI Kanban Coach Phase 0...
echo.
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)
echo Activating virtual environment...
call .venv\Scripts\activate.bat
echo.
echo Installing requirements...
pip install -r requirements.txt
echo.
echo Running doctor...
python -m sami_kanban_coach.cli doctor
echo.
echo.
echo Install complete. You can now use the other scripts.
pause
