# Запуск Interview Trainer без чёрного окна: все сообщения показываются обычными окнами Windows.
# Сервер работает в фоне и сам завершается после закрытия вкладки. Журнал: data\logs\server.err.log
Add-Type -AssemblyName System.Windows.Forms
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
$title = 'Interview Trainer'
$port = 8000
$buttons = [System.Windows.Forms.MessageBoxButtons]
$icons = [System.Windows.Forms.MessageBoxIcon]
$answers = [System.Windows.Forms.DialogResult]

# невидимое окно-владелец поднимает сообщения поверх остальных окон
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true

function Show-Message([string]$text, $buttonSet, $icon) {
    return [System.Windows.Forms.MessageBox]::Show($owner, $text, $title, $buttonSet, $icon)
}

function Test-Url([string]$url) {
    try {
        Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Get-PortOwners {
    return @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess })
}

function Get-LmStudioHint {
    $fallback = 'Откройте LM Studio, загрузите модель Qwen (контекст 8192) и включите Local Server (порт 1234).'
    try {
        $env:PYTHONIOENCODING = 'utf-8'
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        $text = & $python -c "from app.config import load_config, lmstudio_hint; print(lmstudio_hint(load_config()))"
        if ($LASTEXITCODE -eq 0 -and $text) { return ($text | Out-String).Trim() }
    } catch { }
    return $fallback
}

try {
    if (-not (Test-Path $python)) {
        Show-Message ("Не найдено окружение .venv. Один раз выполните в PowerShell из папки проекта:`n`npy -3.11 -m venv .venv`n.\.venv\Scripts\python.exe -m pip install -r requirements.txt") $buttons::OK $icons::Error | Out-Null
        exit 1
    }

    # 1. Сервер уже запущен (возможно, со старой версией кода)
    $busy = Get-PortOwners
    if ($busy.Count -gt 0) {
        $answer = Show-Message ("Сервер уже запущен на порту $port, возможно со старой версией кода.`n`nОстановить его и запустить заново?`n`nДа: перезапустить`nНет: просто открыть страницу в браузере") $buttons::YesNo $icons::Question
        if ($answer -eq $answers::No) {
            Start-Process "http://localhost:$port"
            exit 0
        }
        $busy | ForEach-Object { Stop-Process -Id $_ -Force }
        Start-Sleep -Seconds 2
    }

    # 2. LM Studio: напомнить и подождать
    while (-not (Test-Url 'http://localhost:1234/v1/models')) {
        $hint = Get-LmStudioHint
        $answer = Show-Message ("LM Studio не запущен или не отвечает на порту 1234.`n`n$hint`n`nДа: проверить снова`nНет: запустить без LM Studio`nОтмена: выйти") $buttons::YesNoCancel $icons::Warning
        if ($answer -eq $answers::Cancel) { exit 0 }
        if ($answer -eq $answers::No) { break }
    }

    # 3. Сервер в фоне, журнал в data\logs
    $logs = Join-Path $root 'data\logs'
    New-Item -ItemType Directory -Force $logs | Out-Null
    $outLog = Join-Path $logs 'server.out.log'
    $errLog = Join-Path $logs 'server.err.log'
    $server = Start-Process -FilePath $python -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$port" `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog

    # 4. Окошко ожидания, пока загружаются модели
    $wait = New-Object System.Windows.Forms.Form
    $wait.Text = $title
    $wait.StartPosition = 'CenterScreen'
    $wait.FormBorderStyle = 'FixedDialog'
    $wait.ControlBox = $false
    $wait.TopMost = $true
    $wait.ClientSize = New-Object System.Drawing.Size(380, 70)
    $label = New-Object System.Windows.Forms.Label
    $label.Text = 'Запускаю сервер, загружаю модели (15–20 секунд)...'
    $label.AutoSize = $false
    $label.TextAlign = 'MiddleCenter'
    $label.Dock = 'Fill'
    $wait.Controls.Add($label)
    $wait.Show()

    $deadline = (Get-Date).AddSeconds(180)
    $ready = $false
    while ((Get-Date) -lt $deadline -and -not $server.HasExited) {
        [System.Windows.Forms.Application]::DoEvents()
        if (Test-Url "http://127.0.0.1:$port/api/health") { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    $wait.Close()

    if ($ready) {
        Start-Process "http://localhost:$port"
        exit 0
    }

    # 5. Не запустился: показать конец журнала
    $tail = ''
    if (Test-Path $errLog) { $tail = (Get-Content $errLog -Tail 12 -Encoding UTF8 | Out-String).Trim() }
    if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    Show-Message ("Сервер не запустился.`n`n$tail`n`nПолный журнал: data\logs\server.err.log") $buttons::OK $icons::Error | Out-Null
    exit 1
} catch {
    Show-Message ("Не удалось запустить приложение:`n`n$($_.Exception.Message)") $buttons::OK $icons::Error | Out-Null
    exit 1
}
