@echo off
rem One-click launcher for the C86S manager GUI.
rem Double-click this file (or make a desktop shortcut to it).
rem ASCII only on purpose: .cmd is parsed in the OEM codepage.
chcp 65001 >nul 2>nul
cd /d "%~dp0"

set "HASW=0"
where pythonw >nul 2>nul && set "HASW=1"

if "%HASW%"=="1" (
    start "" pythonw "tools\c86mgr.py"
) else (
    start "" python "tools\c86mgr.py"
)
exit /b 0
