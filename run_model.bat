@echo off
rem Double-click to run the model with the default inputs/ and outputs/ folders.
cd /d "%~dp0"
python run_model.py %*
pause
