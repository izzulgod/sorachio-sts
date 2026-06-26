# =============================================================================
# Sorachio-STS: Download Whisper Model
# Downloads ggml-base.en.bin for fast English STT.
# =============================================================================

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$PROJECT_ROOT = Split-Path -Parent $SCRIPT_DIR
$MODEL_DIR = "$PROJECT_ROOT\models\stt"
$MODEL_FILE = "$MODEL_DIR\ggml-base.en.bin"

# Available models (fastest to most accurate):
# ggml-tiny.en.bin   ~75MB  — fastest, less accurate
# ggml-base.en.bin  ~148MB  — good balance (RECOMMENDED)
# ggml-small.en.bin ~488MB  — more accurate
# ggml-medium.en.bin ~1.5GB — very accurate, slow
$MODEL_NAME = "ggml-base.en.bin"
$MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$MODEL_NAME"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Sorachio-STS: Downloading Whisper Model" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

if (Test-Path $MODEL_FILE) {
    $size = (Get-Item $MODEL_FILE).Length
    Write-Host "[OK] Model already exists: $MODEL_FILE ($([Math]::Round($size/1MB, 1)) MB)" -ForegroundColor Green
    exit 0
}

New-Item -ItemType Directory -Path $MODEL_DIR -Force | Out-Null

Write-Host "Downloading $MODEL_NAME (~148 MB)..." -ForegroundColor Yellow
Write-Host "From: $MODEL_URL" -ForegroundColor Dim
Write-Host ""

try {
    # Use WebClient for progress display
    $webClient = New-Object System.Net.WebClient
    $webClient.DownloadFile($MODEL_URL, $MODEL_FILE)
    $size = (Get-Item $MODEL_FILE).Length
    Write-Host ""
    Write-Host "[OK] Downloaded: $MODEL_FILE ($([Math]::Round($size/1MB, 1)) MB)" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Download failed: $_" -ForegroundColor Red
    Write-Host ""
    Write-Host "Manual download:"
    Write-Host "  URL: $MODEL_URL"
    Write-Host "  Save to: $MODEL_FILE"
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Whisper model ready!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
