@echo off
rem run the bot headless from the repo root (this script lives in bat\)
cd /d "%~dp0.."
set PYTHONUTF8=1
python src\bot.py > bot.log 2>&1
