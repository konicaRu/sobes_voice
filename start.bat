@echo off
rem Запуск без чёрного окна: сообщения показываются обычными окнами Windows (start.vbs -> start.ps1).
rem Вариант с консолью для отладки: start-console.bat
start "" wscript.exe "%~dp0start.vbs"
