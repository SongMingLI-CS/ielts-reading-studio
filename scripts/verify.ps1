$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param([scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Verification command failed with exit code $LASTEXITCODE"
    }
}

Invoke-Checked { python -m pytest -q }
Invoke-Checked { python -m compileall -q app }
Invoke-Checked { python -m ruff check app tests }
Invoke-Checked { python -m app.cli --help | Out-Null }

Write-Host "IELTS Reading Studio offline verification passed."
