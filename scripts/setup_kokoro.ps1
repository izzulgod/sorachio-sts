# =============================================================================
# Sorachio-STS: Setup Kokoro TTS
# Installs the kokoro Python TTS library and its dependencies.
# =============================================================================

$ErrorActionPreference = "Continue"

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
Write-Host "  Sorachio-STS: Installing Kokoro TTS" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Kokoro TTS requires PyTorch (~2GB) and related packages."
Write-Host "This may take several minutes depending on your connection."
Write-Host ""

# Check if already installed
$kokoroCheck = & $PYTHON -c "import kokoro; print('ok')" 2>&1
if ($kokoroCheck -eq "ok") {
    Write-Host "[OK] Kokoro is already installed!" -ForegroundColor Green
    exit 0
}

Write-Host "[1/3] Installing PyTorch (CPU version for compatibility)..." -ForegroundColor Yellow
Write-Host "      Note: For GPU support, install PyTorch with CUDA manually."
Write-Host "      See: https://pytorch.org/get-started/locally/"
Write-Host ""

# Install CPU-only torch to avoid massive CUDA download
& $PIP install torch torchaudio --index-url https://download.pytorch.org/whl/cpu --quiet

if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] PyTorch install failed, trying without index-url..." -ForegroundColor Yellow
    & $PIP install torch torchaudio --quiet
}

Write-Host "[2/3] Installing kokoro and ONNX runtime..." -ForegroundColor Yellow
& $PIP install "kokoro>=0.9.2" onnxruntime --quiet

if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] kokoro install failed. Trying alternative..." -ForegroundColor Yellow
    & $PIP install kokoro --quiet
}

Write-Host "[3/3] Installing phonemizer dependencies..." -ForegroundColor Yellow
& $PIP install phonemizer misaki --quiet

# Verify
$kokoroCheck = & $PYTHON -c "import kokoro; print('ok')" 2>&1
if ($kokoroCheck -eq "ok") {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  Kokoro TTS installed successfully!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Test with: python main.py test-tts 'Hello, I am Sorachio!'"
} else {
    Write-Host ""
    Write-Host "[WARN] Kokoro may not be fully installed." -ForegroundColor Yellow
    Write-Host "Error: $kokoroCheck"
    Write-Host ""
    Write-Host "The system will work without TTS — responses will print to console."
    Write-Host "Try: pip install kokoro[onnx]"
}
Write-Host ""
