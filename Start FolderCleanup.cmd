@echo off
cd /d "%~dp0"
python webui.py
if errorlevel 1 pause
