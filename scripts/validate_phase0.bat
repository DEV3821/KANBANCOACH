@echo off
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
python -m sami_kanban_coach.cli validate-phase0
pause
