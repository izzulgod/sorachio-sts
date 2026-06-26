# =============================================================================
# Sorachio-STS: Python Environment Setup
# Installs all Python dependencies into Miniconda base environment.
# =============================================================================

$ErrorActionPreference = "Stop"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$PROJECT_ROOT = Split-Path -Parent $SCRIPT_DIR

# Dynamically resolve Python and Pip paths
$PYTHON = "python"
if (Get-Command python -ErrorAction SilentlyContinue) {
    $PYTHON = (Get-Command python).Source
} elseif (Test-Path "C:\Users\izzulgod\miniconda3\python.exe") {
    $PYTHON = "C:\Users\izzulgod\miniconda3\python.exe"
}

$PIP = "pip"
if (Get-Command pip -ErrorAction SilentlyContinue) {
    $PIP = (Get-Command pip).Source
} elseif (Test-Path "C:\Users\izzulgod\miniconda3\Scripts\pip.exe") {
    $PIP = "C:\Users\izzulgod\miniconda3\Scripts\pip.exe"
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Sorachio-STS: Python Environment Setup" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Verify Python
if (-not (Test-Path $PYTHON)) {
    Write-Host "[ERROR] Python not found at $PYTHON" -ForegroundColor Red
    Write-Host "Please install Miniconda3 from https://docs.conda.io/en/latest/miniconda.html"
    exit 1
}

$pyVersion = & $PYTHON --version 2>&1
Write-Host "[OK] Python: $pyVersion" -ForegroundColor Green

# Upgrade pip
Write-Host ""
Write-Host "[1/5] Upgrading pip..." -ForegroundColor Yellow
& $PYTHON -m pip install --upgrade pip --quiet

# Install core requirements
Write-Host "[2/5] Installing core dependencies..." -ForegroundColor Yellow
& $PIP install httpx aiohttp aiofiles pyyaml "pydantic>=2.6.0" "pydantic-settings>=2.2.0" --quiet

# Install audio dependencies
Write-Host "[3/5] Installing audio dependencies..." -ForegroundColor Yellow
& $PIP install sounddevice soundfile numpy --quiet

# Install VAD (webrtcvad)
Write-Host "[4/5] Installing webrtcvad (VAD)..." -ForegroundColor Yellow
try {
    & $PIP install webrtcvad-wheels --quiet
    Write-Host "      [OK] webrtcvad-wheels installed" -ForegroundColor Green
} catch {
    Write-Host "      [WARN] webrtcvad-wheels failed, trying webrtcvad..." -ForegroundColor Yellow
    try {
        & $PIP install webrtcvad --quiet
        Write-Host "      [OK] webrtcvad installed" -ForegroundColor Green
    } catch {
        Write-Host "      [WARN] webrtcvad not installed — VAD will be disabled" -ForegroundColor Yellow
    }
}

# Install CLI and utility dependencies
Write-Host "[5/5] Installing CLI and utility dependencies..." -ForegroundColor Yellow
& $PIP install "rich>=13.7.0" "typer>=0.12.0" python-dotenv structlog "pytest>=8.0.0" "pytest-asyncio>=0.23.0" --quiet

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Core dependencies installed successfully!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "NOTE: Kokoro TTS is installed separately via setup_kokoro.ps1"
Write-Host "      This keeps TTS optional and avoids large torch downloads"
Write-Host "      if you only want to test the LLM pipeline first."
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. scripts\build_llamacpp.ps1    — Build llama-server binary"
Write-Host "  2. scripts\build_whispercpp.ps1  — Build whisper-cli binary"
Write-Host "  3. scripts\setup_kokoro.ps1      — Install Kokoro TTS"
Write-Host "  4. scripts\start_servers.ps1     — Start LLM servers"
Write-Host "  5. python main.py text           — Test in text mode"
Write-Host ""
