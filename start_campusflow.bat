@echo off
setlocal
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
cd /d "%~dp0"
title CampusFlow
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\launch_campusflow.py" %*
  goto finished
)
for %%V in (3.12 3.11 3.10 3.9 3.8) do (
  py -%%V -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    py -%%V "scripts\launch_campusflow.py" %*
    goto finished
  )
)
python -c "import sys" >nul 2>nul
if not errorlevel 1 (
  python "scripts\launch_campusflow.py" %*
  goto finished
)
echo 未找到 Python。请安装 Python 3.12，并勾选 Add python.exe to PATH，然后重新双击。
:finished
echo.
echo CampusFlow 启动窗口已结束。按任意键关闭窗口。
if /i not "%~1"=="--no-pause" pause >nul
