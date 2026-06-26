# =============================================================================
# Sorachio-STS: Build whisper.cpp
# Clones, builds, and installs whisper-cli binary.
# =============================================================================

$ErrorActionPreference = "Stop"
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
$BUILD_DIR = "$PROJECT_ROOT\.build\whisper.cpp"
$REPO_DIR = "$PROJECT_ROOT\.repos\whisper.cpp"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Sorachio-STS: Building whisper.cpp" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check prerequisites
if ($null -eq $CMAKE -or -not (Test-Path $CMAKE)) {
    Write-Host "[ERROR] CMake not found" -ForegroundColor Red
    exit 1
}

# Check existing binary
$existingBinary = "$BIN_DIR\whisper-cli.exe"
if (Test-Path $existingBinary) {
    Write-Host "[INFO] whisper-cli.exe already exists at: $existingBinary" -ForegroundColor Yellow
    $choice = Read-Host "Rebuild? (y/N)"
    if ($choice -ne "y" -and $choice -ne "Y") {
        Write-Host "Skipping build." -ForegroundColor Green
        exit 0
    }
}

New-Item -ItemType Directory -Path $BIN_DIR -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path $REPO_DIR) -Force | Out-Null

# Clone or update
if (-not (Test-Path "$REPO_DIR\.git")) {
    Write-Host "[1/4] Cloning whisper.cpp..." -ForegroundColor Yellow
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git $REPO_DIR
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] git clone failed" -ForegroundColor Red
        exit 1
    }
}
else {
    Write-Host "[1/4] Updating whisper.cpp..." -ForegroundColor Yellow
    git -C $REPO_DIR pull --depth 1
}

# Configure
Write-Host "[2/4] Configuring with CMake..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path $BUILD_DIR -Force | Out-Null

& $CMAKE `
    -S $REPO_DIR `
    -B $BUILD_DIR `
    -DCMAKE_BUILD_TYPE=Release `
    -DWHISPER_BUILD_TESTS=OFF `
    -DWHISPER_BUILD_EXAMPLES=ON

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] CMake configure failed" -ForegroundColor Red
    exit 1
}

# Build
$cores = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
Write-Host "[3/4] Building with $cores cores..." -ForegroundColor Yellow

# Build ALL targets (not just whisper-cli) so all required DLLs are compiled
& $CMAKE --build $BUILD_DIR --config Release -j $cores

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Build failed" -ForegroundColor Red
    exit 1
}

# Copy binary
Write-Host "[4/4] Installing binary to bin/..." -ForegroundColor Yellow

$possiblePaths = @(
    "$BUILD_DIR\bin\Release\whisper-cli.exe",
    "$BUILD_DIR\Release\whisper-cli.exe",
    "$BUILD_DIR\examples\cli\Release\whisper-cli.exe",
    "$BUILD_DIR\bin\Release\main.exe",
    "$BUILD_DIR\Release\main.exe"
)

$sourceBinary = $null
foreach ($p in $possiblePaths) {
    if (Test-Path $p) {
        $sourceBinary = $p
        break
    }
}

if ($null -eq $sourceBinary) {
    # Search recursively
    $found = Get-ChildItem -Path $BUILD_DIR -Include "whisper-cli.exe", "main.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) {
        $sourceBinary = $found.FullName
        Write-Host "Found at: $sourceBinary" -ForegroundColor Green
    }
    else {
        Write-Host "[ERROR] whisper binary not found after build" -ForegroundColor Red
        exit 1
    }
}

Copy-Item $sourceBinary $existingBinary -Force
Write-Host "[OK] Copied: $sourceBinary" -ForegroundColor Green

# Copy ALL whisper-related DLLs from the build Release dir to bin/
# Without these DLLs, whisper-cli.exe crashes with 0xC0000005 (Access Violation)
$releaseDir = Split-Path $sourceBinary
Write-Host "[4b] Copying whisper DLLs from: $releaseDir" -ForegroundColor Yellow

$dllsCopied = 0
Get-ChildItem -Path $releaseDir -Filter "*.dll" | ForEach-Object {
    # Only copy whisper/ggml DLLs, skip ones already present from llama.cpp
    $destPath = Join-Path $BIN_DIR $_.Name
    Copy-Item $_.FullName $destPath -Force
    Write-Host "  + $($_.Name)" -ForegroundColor Gray
    $dllsCopied++
}

# Also search recursively if no DLLs found next to binary
if ($dllsCopied -eq 0) {
    Write-Host "No DLLs found next to binary, searching recursively..." -ForegroundColor Yellow
    Get-ChildItem -Path $BUILD_DIR -Filter "*.dll" -Recurse | Where-Object {
        $_.FullName -like "*Release*"
    } | ForEach-Object {
        $destPath = Join-Path $BIN_DIR $_.Name
        Copy-Item $_.FullName $destPath -Force
        Write-Host "  + $($_.Name) (from $($_.DirectoryName))" -ForegroundColor Gray
        $dllsCopied++
    }
}

Write-Host "[OK] $dllsCopied DLL(s) copied to bin/" -ForegroundColor Green

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  whisper-cli.exe built successfully!" -ForegroundColor Green
Write-Host "  Location: $existingBinary" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next: Run scripts\download_whisper_model.ps1 to get the model"
Write-Host ""