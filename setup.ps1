$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Python = (Get-Command py -ErrorAction Stop).Source
& $Python -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\.venv\Scripts\python.exe scripts\download_models.py
& .\.venv\Scripts\python.exe scripts\verify_models.py
& .\.venv\Scripts\python.exe scripts\ocr_smoke_test.py --mode server
& .\.venv\Scripts\python.exe scripts\ocr_smoke_test.py --mode server --offline
Write-Host "安装及离线验证完成。运行 .\run.ps1 启动。"

