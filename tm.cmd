@echo off
rem SPDX-License-Identifier: Apache-2.0
rem Copyright 2026 The esp32-tailscale Authors
rem Windows entry point (cmd / PowerShell):  .\tm help   .\tm build   .\tm flash-monitor
rem Same commands as `make <target>` on Linux/macOS; see scripts\tm.py.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0scripts\tm.py" %*
) else (
  python "%~dp0scripts\tm.py" %*
)
exit /b %errorlevel%
