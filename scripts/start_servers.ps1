# =============================================================================
# Sorachio-STS: Start LLM Servers
# Launches both llama-server instances for LLM #1 and LLM #2.
# =============================================================================

$ErrorActionPreference = "Continue"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$PROJECT_ROOT = Split-Path -Parent $SCRIPT_DIR
$BINARY = "$PROJECT_ROOT\bin\llama-server.exe"

# Model paths
$LLM1_MODEL = "$PROJECT_ROOT\models\llm1\Qwen3-0.6B-Q8_0.gguf"
$LLM2_MODEL = "$PROJECT_ROOT\models\llm2\gemma-3-1b-it-Q8_0.gguf"

# Server config
$LLM1_PORT = 8001
$LLM2_PORT = 8002
$SRV_HOST = "127.0.0.1"
$CTX = 2048
$THREADS = 4
$GPU_LAYERS = 0      # Set to -1 for full GPU, or N for N layers

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Sorachio-STS: Starting LLM Servers" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check binary
if (-not (Test-Path $BINARY)) {
    Write-Host "[ERROR] llama-server.exe not found at: $BINARY" -ForegroundColor Red
    Write-Host "Run: scripts\build_llamacpp.ps1"
    exit 1
}

# Check models
if (-not (Test-Path $LLM1_MODEL)) {
    Write-Host "[ERROR] LLM #1 model not found: $LLM1_MODEL" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $LLM2_MODEL)) {
    Write-Host "[ERROR] LLM #2 model not found: $LLM2_MODEL" -ForegroundColor Red
    exit 1
}

$LOG_DIR = "$PROJECT_ROOT\logs"
New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null

# Kill any existing servers on these ports
Write-Host "Checking for existing servers..." -ForegroundColor Yellow
$existing1 = Get-NetTCPConnection -LocalPort $LLM1_PORT -ErrorAction SilentlyContinue
$existing2 = Get-NetTCPConnection -LocalPort $LLM2_PORT -ErrorAction SilentlyContinue
if ($existing1) {
    Write-Host "[INFO] Port $LLM1_PORT in use, attempting to free it..."
    $pid1 = $existing1.OwningProcess | Select-Object -First 1
    Stop-Process -Id $pid1 -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}
if ($existing2) {
    Write-Host "[INFO] Port $LLM2_PORT in use, attempting to free it..."
    $pid2 = $existing2.OwningProcess | Select-Object -First 1
    Stop-Process -Id $pid2 -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

# Start LLM #1 — Cognitive Gateway
Write-Host ""
Write-Host "[1/2] Starting Cognitive Gateway (LLM #1) on port $LLM1_PORT..." -ForegroundColor Yellow
Write-Host "      Model: $(Split-Path $LLM1_MODEL -Leaf)"
$proc1 = Start-Process -FilePath $BINARY `
    -ArgumentList "--model `"$LLM1_MODEL`" --port $LLM1_PORT --host $SRV_HOST --ctx-size $CTX --threads $THREADS --n-gpu-layers $GPU_LAYERS --log-disable --no-mmap" `
    -RedirectStandardOutput "$LOG_DIR\llm1_server.log" `
    -RedirectStandardError "$LOG_DIR\llm1_server_err.log" `
    -PassThru `
    -WindowStyle Hidden
Write-Host "      PID: $($proc1.Id)" -ForegroundColor Green

# Start LLM #2 — Personality Core
Write-Host "[2/2] Starting Personality Core (LLM #2) on port $LLM2_PORT..." -ForegroundColor Yellow
Write-Host "      Model: $(Split-Path $LLM2_MODEL -Leaf)"
$proc2 = Start-Process -FilePath $BINARY `
    -ArgumentList "--model `"$LLM2_MODEL`" --port $LLM2_PORT --host $SRV_HOST --ctx-size 4096 --threads $THREADS --n-gpu-layers $GPU_LAYERS --log-disable --no-mmap" `
    -RedirectStandardOutput "$LOG_DIR\llm2_server.log" `
    -RedirectStandardError "$LOG_DIR\llm2_server_err.log" `
    -PassThru `
    -WindowStyle Hidden
Write-Host "      PID: $($proc2.Id)" -ForegroundColor Green

# Wait for startup and check
Write-Host ""
Write-Host "Waiting for servers to start (30s timeout)..." -ForegroundColor Yellow
$timeout = 30
$started = $false
for ($i = 0; $i -lt $timeout; $i++) {
    Start-Sleep -Seconds 1
    $check1 = try { Invoke-WebRequest -Uri "http://$SRV_HOST`:$LLM1_PORT/health" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop; $true } catch { $false }
    $check2 = try { Invoke-WebRequest -Uri "http://$SRV_HOST`:$LLM2_PORT/health" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop; $true } catch { $false }
    if ($check1 -and $check2) {
        $started = $true
        break
    }
    Write-Host "  Waiting... ($($i+1)s) LLM1=$check1 LLM2=$check2" -ForegroundColor DarkGray
}

Write-Host ""
if ($started) {
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  Both LLM servers are ready!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  LLM #1 (Cognitive Gateway): http://$SRV_HOST`:$LLM1_PORT" -ForegroundColor Cyan
    Write-Host "  LLM #2 (Personality Core):  http://$SRV_HOST`:$LLM2_PORT" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Now run:" -ForegroundColor Yellow
    Write-Host "  python main.py text --no-servers    # Text mode (servers already running)"
    Write-Host "  python main.py run  --no-servers    # Full voice mode"
} else {
    Write-Host "[WARN] Servers may not be fully ready yet." -ForegroundColor Yellow
    Write-Host "Check logs at: $LOG_DIR"
    Write-Host ""
    Write-Host "If servers are slow to start, wait 10-20 more seconds"
    Write-Host "then test with: Invoke-WebRequest http://$SRV_HOST`:$LLM1_PORT/health -UseBasicParsing"
}
Write-Host ""
