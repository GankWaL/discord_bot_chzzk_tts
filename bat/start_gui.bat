@echo off
rem launch the control panel from the repo root (this script lives in bat\)
cd /d "%~dp0.."

rem swap in rebuilt GUI exe if present
if exist dist\tts_bot_gui_new.exe (
    if exist dist\tts_bot_gui.exe del /f dist\tts_bot_gui.exe
    move /y dist\tts_bot_gui_new.exe dist\tts_bot_gui.exe >nul
)

rem prefer exe build, fall back to python source
if exist dist\tts_bot_gui.exe (
    start "" dist\tts_bot_gui.exe
) else (
    start "" pythonw src\bot_gui.py
)
