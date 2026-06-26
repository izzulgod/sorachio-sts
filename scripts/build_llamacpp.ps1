# =============================================================================
# Sorachio-STS: Build llama.cpp
# Clones, builds, and installs llama-server binary.
# =============================================================================

$ErrorActionPreference = "Continue"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$PROJECT_ROOT = Split-Path -Parent $SCRIPT_DIR
# Dynamically resolve CMake path
$CMAKE = $null
if (Get-Command cmake -ErrorAction SilentlyContinue) {
    $CMAKE = (Get-Command cmake).Source
} elseif (Test-Path "C:\Program Files\CMake\bin\cmake.exe") {
    $CMAKE = "C:\Program Files\CMake\bin\cmake.exe"
}

$BIN_DIR = "$PROJECT_ROOT\bin"
$BUILD_DIR = "$PROJECT_ROOT\.build\llama.cpp"
$REPO_DIR = "$PROJECT_ROOT\.repos\llama.cpp"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Sorachio-STS: Building llama.cpp" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check prerequisites
if ($null -eq $CMAKE -or -not (Test-Path $CMAKE)) {
    Write-Host "[ERROR] CMake not found at $CMAKE" -ForegroundColor Red
    Write-Host "Install from: https://cmake.org/download/"
    exit 1
}
$cmakeVersion = & $CMAKE --version | Select-Object -First 1
Write-Host "[OK] CMake: $cmakeVersion" -ForegroundColor Green

# Check for existing llama-server
$existingBinary = "$BIN_DIR\llama-server.exe"
if (Test-Path $existingBinary) {
    Write-Host ""
    Write-Host "[INFO] llama-server.exe already exists at: $existingBinary" -ForegroundColor Yellow
    $choice = Read-Host "Rebuild? (y/N)"
    if ($choice -ne "y" -and $choice -ne "Y") {
        Write-Host "Skipping build." -ForegroundColor Green
        exit 0
    }
}

# Create directories
New-Item -ItemType Directory -Path $BIN_DIR -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path $REPO_DIR) -Force | Out-Null

# Clone or update
if (-not (Test-Path "$REPO_DIR\.git")) {
    Write-Host "[1/4] Cloning llama.cpp..." -ForegroundColor Yellow
    git clone --depth 1 https://github.com/ggml-org/llama.cpp.git $REPO_DIR
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] git clone failed" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[1/4] Updating llama.cpp..." -ForegroundColor Yellow
    git -C $REPO_DIR pull --depth 1
}

# Configure with CMake
Write-Host "[2/4] Configuring with CMake..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path $BUILD_DIR -Force | Out-Null

& $CMAKE `
    -S $REPO_DIR `
    -B $BUILD_DIR `
    -DCMAKE_BUILD_TYPE=Release `
    -DLLAMA_BUILD_SERVER=ON `
    -DLLAMA_CURL=OFF `
    -DLLAMA_BUILD_TESTS=OFF `
    -DLLAMA_BUILD_EXAMPLES=ON

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] CMake configure failed" -ForegroundColor Red
    exit 1
}

# Build (use all CPU cores)
$cores = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
Write-Host "[3/4] Building with $cores cores (this may take 5-15 minutes)..." -ForegroundColor Yellow
& $CMAKE --build $BUILD_DIR --config Release --target llama-server -j $cores

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Build failed" -ForegroundColor Red
    exit 1
}

# Copy binary to bin/
Write-Host "[4/4] Installing binary to bin/..." -ForegroundColor Yellow

# Find the built binary (location varies by CMake version/OS)
$possiblePaths = @(
    "$BUILD_DIR\bin\Release\llama-server.exe",
    "$BUILD_DIR\Release\llama-server.exe",
    "$BUILD_DIR\llama-server.exe",
    "$BUILD_DIR\examples\server\Release\llama-server.exe"
)

$sourceBinary = $null
foreach ($p in $possiblePaths) {
    if (Test-Path $p) {
        $sourceBinary = $p
        break
    }
}

if ($null -eq $sourceBinary) {
    Write-Host "[ERROR] Built binary not found. Searched:" -ForegroundColor Red
    $possiblePaths | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    Write-Host "Trying to find it..." -ForegroundColor Yellow
    $found = Get-ChildItem -Path $BUILD_DIR -Filter "llama-server.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) {
        $sourceBinary = $found.FullName
        Write-Host "Found at: $sourceBinary" -ForegroundColor Green
    } else {
        exit 1
    }
}

Copy-Item $sourceBinary $existingBinary -Force
$sourceDir = Split-Path $sourceBinary
Get-ChildItem -Path $sourceDir -Filter "*.dll" -ErrorAction SilentlyContinue | Copy-Item -Destination $BIN_DIR -Force
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  llama-server.exe built successfully!" -ForegroundColor Green
Write-Host "  Location: $existingBinary" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
