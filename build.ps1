$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$env:PADDLE_PDX_EAGER_INIT = "False"
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = "True"
$env:DISABLE_MODEL_SOURCE_CHECK = "True"
$env:PADDLE_DISABLE_TELEMETRY = "1"
& .\.venv\Scripts\python.exe scripts\verify_models.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean BankReceiptTool.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$Dist = Join-Path $Root 'dist\BankReceiptTool_v0_3_11'
Get-ChildItem -File -Filter 'README*.md' | Copy-Item -Destination $Dist
Copy-Item -LiteralPath 'LICENSE' -Destination $Dist
Copy-Item -LiteralPath 'NOTICE','THIRD_PARTY_NOTICES.md','RELEASE_NOTES.md','SOURCE_CODE.md' -Destination $Dist
Copy-Item -LiteralPath 'licenses' -Destination $Dist -Recurse -Force
Write-Host "Windows build ready: $Dist"
