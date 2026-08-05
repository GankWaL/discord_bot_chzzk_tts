@echo off
rem First-time setup: opens the control panel GUI.
rem In the GUI: enter tokens in the settings dialog, then click
rem [Install libraries] and [Rebuild exe].
cd /d "%~dp0.."
python src\bot_gui.py
if errorlevel 1 pause
