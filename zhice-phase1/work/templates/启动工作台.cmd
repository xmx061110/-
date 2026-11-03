@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
py -3 --version >nul 2>&1
if not errorlevel 1 goto use_py
python --version >nul 2>&1
if not errorlevel 1 goto use_python
echo Please install Python 3.11 or newer and add it to PATH.
pause
exit /b 1
:use_py
py -3 -X utf8 "启动.py"
goto finish
:use_python
python -X utf8 "启动.py"
:finish
if errorlevel 1 pause
