@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Interview Trainer

if not exist ".venv\Scripts\python.exe" (
    echo Не найдено окружение .venv. Один раз выполните:
    echo   py -3.11 -m venv .venv
    echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
    echo Сервер уже запущен на порту 8000, возможно со старой версией кода.
    echo Его окно может быть свёрнуто на панели задач.
    choice /c YN /m "Остановить старый сервер и запустить заново"
    if errorlevel 2 exit /b 0
    powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
    timeout /t 2 /nobreak >nul
    echo.
)

set PYTHONIOENCODING=utf-8
:check_lm
powershell -NoProfile -Command "try { Invoke-WebRequest http://localhost:1234/v1/models -UseBasicParsing -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    echo.
    echo ВНИМАНИЕ: LM Studio не запущен или не отвечает на порту 1234.
    ".venv\Scripts\python.exe" -c "from app.config import load_config, lmstudio_hint; print(lmstudio_hint(load_config()))"
    echo.
    choice /c RSX /m "R - проверить снова, S - запустить без LM Studio, X - выйти"
    if errorlevel 3 exit /b 0
    if errorlevel 2 goto start_server
    goto check_lm
)

:start_server
echo Запускаю сервер, модели загружаются около 15-20 секунд...
echo Браузер откроется сам. Сервер сам завершится через 2,5 минуты после закрытия вкладки (или Ctrl+C).
echo.

start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 90; $i++) { try { Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process 'http://localhost:8000'; break } catch { Start-Sleep -Seconds 2 } }"

".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 pause
