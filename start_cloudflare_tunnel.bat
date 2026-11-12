@echo off
setlocal
title YOLOv26 - Web, AI, Zalo, Cloudflare
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Khong tim thay Python trong PATH.
    exit /b 1
)

python -c "import uvicorn, psutil" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Thieu uvicorn hoac psutil. Cai dat: pip install -r backend\requirements.txt
    exit /b 1
)

python -u scripts\dev_stack.py %*
exit /b %errorlevel%
