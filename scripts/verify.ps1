$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param([scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Verification command failed with exit code $LASTEXITCODE"
    }
}

# Offline verification: both test suites, byte-compilation, lint and CLI smoke tests.
# No API key is needed and no provider call is made.
Invoke-Checked { python -m pytest -q }
Invoke-Checked { python -m compileall -q app components/context-novel/src }
Invoke-Checked { python -m ruff check app tests migrations components/context-novel/src components/context-novel/tests }
Invoke-Checked { python -m app.cli --help | Out-Null }
Invoke-Checked { python -m app.cli migrate --help | Out-Null }
Invoke-Checked { python -m app.cli worker --help | Out-Null }

Write-Host "IELTS Reading Studio offline verification passed."
