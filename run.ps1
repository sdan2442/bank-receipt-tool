$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$Root\.venv\Scripts\pythonw.exe" "$Root\run.py"

