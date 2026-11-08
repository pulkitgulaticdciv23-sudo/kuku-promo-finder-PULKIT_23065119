@echo off
setlocal
cd /d "%~dp0"
title Kuku Promo Finder
echo Starting Kuku Promo Finder...
echo.
where py >nul 2>&1
if errorlevel 1 goto python_missing
if not exist ".venv\Scripts\python.exe" (
    echo Preparing a local Python environment. This may take a moment...
    py -3 -m venv ".venv"
    if errorlevel 1 goto setup_error
)
".venv\Scripts\python.exe" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo Installing Streamlit for the first run. Internet is needed for this step...
    ".venv\Scripts\python.exe" -m pip install "streamlit>=1.40,<2"
    if errorlevel 1 goto setup_error
)
echo.
echo The app should open in your browser. If it does not, open http://localhost:8501
echo Keep this window open while using the app. Press Ctrl+C here to stop it.
echo.
".venv\Scripts\python.exe" -m streamlit run "app.py" --server.address 127.0.0.1 --browser.gatherUsageStats false
if errorlevel 1 goto app_error
exit /b 0

:python_missing
echo Python was not found. Please install Python 3.10 or newer and reopen this file.
pause
exit /b 1

:setup_error
echo Setup did not complete. Please take a screenshot of this window so the issue can be checked.
pause
exit /b 1

:app_error
echo The app stopped with an error. Please take a screenshot of this window so the issue can be checked.
pause
exit /b 1
