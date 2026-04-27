@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo Starting GPT Image Generator...
python -m gpt_image_app
if %errorlevel% neq 0 (
    echo.
    echo Failed to start. Check Python installation.
    pause
)
