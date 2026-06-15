@echo off
cd /d "%~dp0"

echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt

echo [2/3] Starting local website on http://127.0.0.1:5000 ...
start "Lumina Local Server" cmd /k python app.py

timeout /t 4 >nul

echo [3/3] Starting Cloudflare public tunnel...
set CLOUDFLARED_PATH=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\cloudflared.exe
if exist "%CLOUDFLARED_PATH%" (
    start "Lumina Public Tunnel" cmd /k "%CLOUDFLARED_PATH%" tunnel --url http://127.0.0.1:5000 --no-autoupdate
) else (
    echo cloudflared not found. Please install it first with:
    echo winget install --id Cloudflare.cloudflared -e
)
