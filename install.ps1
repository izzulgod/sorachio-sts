# =============================================================================
# Sorachio-STS: Master Installation Script
# Orchestrates python environment setup, binary builds, and model downloads.
# =============================================================================

$ErrorActionPreference = "Stop"
$PROJECT_ROOT = $PSScriptRoot

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Sorachio-STS: Installer & Setup Orchestrator" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This script will guide you through setting up Sorachio-STS:"
Write-Host "  1. Python dependencies setup"
Write-Host "  2. Building llama-server (llama.cpp) for LLMs"
Write-Host "  3. Building whisper-cli (whisper.cpp) for Speech-to-Text"
Write-Host "  4. Downloading the default Whisper model"
Write-Host "  5. Optional setup for Kokoro TTS"
Write-Host ""

# Step 1: Install Python dependencies
Write-Host "------------------------------------------------------------" -ForegroundColor Gray
Write-Host "Step 1/5: Setting up Python Environment..." -ForegroundColor Yellow
try {
    & "$PROJECT_ROOT\scripts\setup_env.ps1"
} catch {
    Write-Host "[ERROR] Python environment setup failed." -ForegroundColor Red
    exit 1
}

# Step 2: Build llama.cpp
Write-Host "------------------------------------------------------------" -ForegroundColor Gray
Write-Host "Step 2/5: Building llama.cpp (LLM server)..." -ForegroundColor Yellow
$buildLlama = Read-Host "Do you want to build llama.cpp now? (Y/n)"
if ($buildLlama -eq "" -or $buildLlama -eq "y" -or $buildLlama -eq "Y") {
    try {
        & "$PROJECT_ROOT\scripts\build_llamacpp.ps1"
    } catch {
        Write-Host "[WARN] Building llama.cpp failed. You will need to build it manually before running the system." -ForegroundColor Yellow
    }
} else {
    Write-Host "Skipped." -ForegroundColor Gray
}

# Step 3: Build whisper.cpp
Write-Host "------------------------------------------------------------" -ForegroundColor Gray
Write-Host "Step 3/5: Building whisper.cpp (Speech-to-Text)..." -ForegroundColor Yellow
$buildWhisper = Read-Host "Do you want to build whisper.cpp now? (Y/n)"
if ($buildWhisper -eq "" -or $buildWhisper -eq "y" -or $buildWhisper -eq "Y") {
    try {
        & "$PROJECT_ROOT\scripts\build_whispercpp.ps1"
    } catch {
        Write-Host "[WARN] Building whisper.cpp failed. You will need to build it manually before running." -ForegroundColor Yellow
    }
} else {
    Write-Host "Skipped." -ForegroundColor Gray
}

# Step 4: Download Whisper Model
Write-Host "------------------------------------------------------------" -ForegroundColor Gray
Write-Host "Step 4/5: Downloading Whisper model..." -ForegroundColor Yellow
$dlModel = Read-Host "Do you want to download the default Whisper model? (Y/n)"
if ($dlModel -eq "" -or $dlModel -eq "y" -or $dlModel -eq "Y") {
    try {
        & "$PROJECT_ROOT\scripts\download_whisper_model.ps1"
    } catch {
        Write-Host "[WARN] Downloading Whisper model failed. Check your network or runscripts\download_whisper_model.ps1 later." -ForegroundColor Yellow
    }
} else {
    Write-Host "Skipped." -ForegroundColor Gray
}

# Step 5: Setup Kokoro TTS
Write-Host "------------------------------------------------------------" -ForegroundColor Gray
Write-Host "Step 5/5: Setting up Kokoro TTS (Optional)..." -ForegroundColor Yellow
Write-Host "Kokoro TTS offers high-quality voice synthesis but requires downloading PyTorch (~2GB)."
$setupKokoro = Read-Host "Do you want to setup Kokoro TTS? (y/N)"
if ($setupKokoro -eq "y" -or $setupKokoro -eq "Y") {
    try {
        & "$PROJECT_ROOT\scripts\setup_kokoro.ps1"
    } catch {
        Write-Host "[WARN] Kokoro TTS setup failed. The system can still run in text mode or print response transcripts." -ForegroundColor Yellow
    }
} else {
    Write-Host "Skipped Kokoro TTS setup. Responses will print to terminal console." -ForegroundColor Gray
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Sorachio-STS Setup Completed!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "To download the LLM model weights, please place them in:"
Write-Host "  - LLM 1 (Cognitive Gateway): models\llm1\Qwen3-0.6B-Q8_0.gguf"
Write-Host "  - LLM 2 (Personality Core): models\llm2\gemma-3-1b-it-Q8_0.gguf"
Write-Host ""
Write-Host "How to Run:" -ForegroundColor Cyan
Write-Host "  1. Start the LLM servers in a separate terminal:"
Write-Host "     .\scripts\start_servers.ps1"
Write-Host ""
Write-Host "  2. Run the companion system:"
Write-Host "     - Full Voice Mode (Microphone + Speaker):"
Write-Host "       python main.py run --no-servers"
Write-Host ""
Write-Host "     - Text Mode (Terminal only):"
Write-Host "       python main.py text --no-servers"
Write-Host ""
