@echo off
chcp 65001 >nul
REM AgentSoc 代码同步到 GitHub（走 api.github.com，不依赖 github.com 直连）
REM
REM 用法（在本机任意终端执行）：
REM   push_github.cmd <你的GitHub PAT>
REM 或先预览不提交：
REM   push_github.cmd <你的GitHub PAT> --dry-run

set PY=C:/Users/jjqin/.workbuddy/binaries/python/versions/3.13.12/python.exe
set SCRIPT=%~dp0push_via_api.py

if "%~1"=="" (
  echo 用法: push_github.cmd ^<GitHub PAT^> [--dry-run]
  exit /b 1
)

"%PY%" "%SCRIPT%" --token "%~1" %2
