#!/usr/bin/env python3
"""
MBG: Master Bootstrap Guardian
==============================
Automated Build & Compatibility System for Sorachio-STS.
Handles environment setup, dependency installation, binary compilation,
and model downloads automatically.

Usage:
    python mbg.py              # Full bootstrap
    python mbg.py --check      # Check system status only
    python mbg.py --force      # Force rebuild everything
    python mbg.py --models     # Download models only
    python mbg.py --build      # Build binaries only
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional

# ============================================================================
# MBG Configuration
# ============================================================================

MBG_VERSION = "1.0.0"
MBG_NAME = "Master Bootstrap Guardian"
MBG_TAGLINE = "Automated Build & Compatibility System for Sorachio-STS"

# Supported Python versions
PYTHON_MIN = (3, 10)
PYTHON_MAX = (3, 12)

# Project structure
PROJECT_ROOT = Path(__file__).parent.absolute()
BIN_DIR = PROJECT_ROOT / "bin"
REPOS_DIR = PROJECT_ROOT / ".repos"
MODELS_DIR = PROJECT_ROOT / "models"
VENV_DIR = PROJECT_ROOT / "venv_runtime"

# Model configurations
MODELS = {
    "stt": {
        "dir": MODELS_DIR / "stt",
        "file": "ggml-base.en.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin",
        "description": "Whisper STT model (148MB)",
    },
    "llm1": {
        "dir": MODELS_DIR / "llm1",
        "file": "Qwen3-0.6B-Q8_0.gguf",
        "url": "https://huggingface.co/unsloth/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf",
        "description": "Qwen3-0.6B for Cognitive Gateway (639MB)",
    },
    "llm2": {
        "dir": MODELS_DIR / "llm2",
        "file": "gemma-3-1b-it-Q8_0.gguf",
        "url": "https://huggingface.co/unsloth/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q8_0.gguf",
        "description": "gemma-3-1b-it for Personality Core (1.07GB)",
    },
}

# Binary configurations
BINARIES = {
    "llama-server": {
        "repo": "llama.cpp",
        "url": "https://github.com/ggerganov/llama.cpp",
        "build_args": ["-DLLAMA_BUILD_SERVER=ON"],
        "check_args": ["--version"],
    },
    "whisper-cli": {
        "repo": "whisper.cpp",
        "url": "https://github.com/ggerganov/whisper.cpp",
        "build_args": [],
        "check_args": ["--help"],
    },
}

# ============================================================================
# Logging Setup
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="[MBG] %(levelname)s: %(message)s"
)
log = logging.getLogger("mbg")


# ============================================================================
# MBG Core Class
# ============================================================================

class MasterBootstrapGuardian:
    """
    Master Bootstrap Guardian - Automated Build & Compatibility System.
    
    Handles:
    - Python version checking and relaunching
    - Virtual environment creation
    - Dependency installation
    - Binary compilation (llama.cpp, whisper.cpp)
    - Model downloads (STT, LLM1, LLM2)
    - Platform compatibility verification
    """

    def __init__(self, force: bool = False, check_only: bool = False):
        self.force = force
        self.check_only = check_only
        self.current_arch = platform.machine()
        self.current_platform = sys.platform

    def run(self) -> None:
        """Main entry point for MBG."""
        self._print_banner()
        
        # 1. Check Python version
        self._check_python_version()
        
        if self.check_only:
            self._print_status()
            return
        
        # 2. Setup virtual environment
        self._setup_venv()
        
        # 3. Install dependencies
        self._install_dependencies()
        
        # 4. Build binaries
        self._build_binaries()
        
        # 5. Download models
        self._download_models()
        
        # 6. Final status
        self._print_status()
        log.info("MBG: Master Bootstrap Guardian - System ready!")

    def _print_banner(self) -> None:
        """Print MBG banner."""
        print()
        print("=" * 60)
        print(f"  MBG: Master Bootstrap Guardian v{MBG_VERSION}")
        print(f"  {MBG_TAGLINE}")
        print("=" * 60)
        print()

    def _check_python_version(self) -> None:
        """Check if Python version is compatible."""
        major, minor = sys.version_info[:2]
        
        if major != 3 or not (PYTHON_MIN[1] <= minor <= PYTHON_MAX[1]):
            log.warning(
                f"Python {major}.{minor} is outside compatible range "
                f"({PYTHON_MIN[0]}.{PYTHON_MIN[1]} - {PYTHON_MAX[0]}.{PYTHON_MAX[1]})"
            )
            self._relaunch_with_compatible_python()
        
        log.info(f"Python {major}.{minor} is compatible")

    def _relaunch_with_compatible_python(self) -> None:
        """Find and relaunch with a compatible Python version."""
        log.info("Searching for compatible Python version...")
        
        for version in range(PYTHON_MAX[1], PYTHON_MIN[1] - 1, -1):
            exe_names = [f"python3.{version}", f"python{version}"]
            
            for exe_name in exe_names:
                exe_path = shutil.which(exe_name)
                if exe_path:
                    log.info(f"Found Python {version}: {exe_path}")
                    log.info("Relaunching with compatible Python...")
                    subprocess.run([exe_path] + sys.argv)
                    sys.exit(0)
        
        log.error("No compatible Python version found!")
        sys.exit(1)

    def _setup_venv(self) -> None:
        """Create and activate virtual environment."""
        if self._is_in_venv():
            log.info("Already running in virtual environment")
            return
        
        log.info("Setting up virtual environment...")
        VENV_DIR.mkdir(parents=True, exist_ok=True)
        
        # Create venv
        subprocess.run(
            [sys.executable, "-m", "venv", str(VENV_DIR)],
            check=True
        )
        
        # Get venv Python path
        if os.name == "nt":
            venv_python = VENV_DIR / "Scripts" / "python.exe"
        else:
            venv_python = VENV_DIR / "bin" / "python"
        
        log.info(f"Restarting with venv Python: {venv_python}")
        subprocess.run([str(venv_python)] + sys.argv)
        sys.exit(0)

    def _is_in_venv(self) -> bool:
        """Check if running inside a virtual environment."""
        return sys.prefix != sys.base_prefix

    def _install_dependencies(self) -> None:
        """Install required Python packages."""
        log.info("Installing dependencies...")
        
        # Upgrade pip first
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
            capture_output=True,
            check=True
        )
        
        # Core dependencies
        deps = [
            "httpx",
            "aiohttp",
            "aiofiles",
            "pyyaml",
            "pydantic>=2.6.0",
            "pydantic-settings>=2.2.0",
            "sounddevice",
            "soundfile",
            "numpy",
            "rich>=13.7.0",
            "typer>=0.12.0",
            "python-dotenv",
            "structlog",
            "kokoro>=0.9.2",
            "misaki[en]",         # kokoro G2P engine (English phonemizer)
        ]
        
        log.info(f"Installing {len(deps)} packages...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install"] + deps,
            capture_output=True,
            check=True
        )
        
        # VAD (try wheels first, fallback to source)
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "webrtcvad-wheels"],
                capture_output=True,
                check=True
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "webrtcvad"],
                capture_output=True,
                check=True
            )
        
        log.info("Dependencies installed successfully")

    def _build_binaries(self) -> None:
        """Build external binaries (llama.cpp, whisper.cpp)."""
        log.info("Building binaries...")
        
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        REPOS_DIR.mkdir(parents=True, exist_ok=True)
        
        # Check for required build tools
        self._check_build_tools()
        
        # Build each binary
        for binary_name, config in BINARIES.items():
            self._build_binary(binary_name, config)

    def _check_build_tools(self) -> None:
        """Check if required build tools are installed."""
        required_tools = ["cmake", "git"]
        
        for tool in required_tools:
            if not shutil.which(tool):
                log.warning(f"Build tool '{tool}' not found")
                self._install_build_tool(tool)

    def _install_build_tool(self, tool: str) -> None:
        """Install a build tool using system package manager."""
        log.info(f"Installing {tool}...")
        
        if sys.platform == "darwin":
            # macOS - use Homebrew
            subprocess.run(["brew", "install", tool], check=False)
        elif sys.platform.startswith("linux"):
            # Linux - use apt
            subprocess.run(["sudo", "apt-get", "install", "-y", tool], check=False)
        else:
            log.warning(f"Cannot auto-install {tool} on {sys.platform}")

    def _build_binary(self, name: str, config: dict) -> None:
        """Build a single binary."""
        binary_path = self._get_binary_path(name)
        repo_path = REPOS_DIR / config["repo"]
        
        # Check if binary is valid
        if not self.force and self._is_binary_valid(binary_path, config["check_args"]):
            log.info(f"{name} is valid, skipping build")
            return
        
        log.info(f"Building {name}...")
        
        # Clone or update repository
        if not repo_path.exists():
            log.info(f"Cloning {config['repo']}...")
            subprocess.run(
                ["git", "clone", config["url"], str(repo_path)],
                check=True
            )
        else:
            log.info(f"Updating {config['repo']}...")
            subprocess.run(
                ["git", "-C", str(repo_path), "pull"],
                capture_output=True,
                check=True
            )
        
        # Build
        build_dir = repo_path / "build"
        
        # Configure
        cmake_args = ["cmake", "-B", str(build_dir)] + config["build_args"]
        subprocess.run(cmake_args, cwd=repo_path, check=True)
        
        # Compile
        threads = os.cpu_count() or 1
        log.info(f"Compiling with {threads} threads...")
        subprocess.run(
            ["cmake", "--build", str(build_dir), "--config", "Release", "-j", str(threads)],
            cwd=repo_path,
            check=True
        )
        
        # Copy binary — handle .exe suffix on Windows
        exe_suffix = ".exe" if os.name == "nt" else ""
        if name == "llama-server":
            src_bin = build_dir / "bin" / f"llama-server{exe_suffix}"
        else:  # whisper-cli
            src_bin = build_dir / "bin" / f"main{exe_suffix}"
        
        if src_bin.exists():
            shutil.copy(src_bin, binary_path)
            log.info(f"{name} built successfully")
        else:
            log.warning(f"Could not find {name} binary after build")

    def _get_binary_path(self, name: str) -> Path:
        """Return platform-correct binary path (.exe on Windows)."""
        if os.name == "nt":
            return BIN_DIR / f"{name}.exe"
        return BIN_DIR / name

    def _is_binary_valid(self, binary_path: Path, check_args: list[str]) -> bool:
        """Check if a binary exists and is functional."""
        if not binary_path.exists():
            return False

        # Skip 'file' command on Windows (Unix-only tool).
        # On Unix, optionally verify architecture.
        if os.name != "nt":
            try:
                result = subprocess.run(
                    ["file", str(binary_path)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and self.current_arch not in result.stdout:
                    log.warning(f"Architecture mismatch for {binary_path.name}")
                    return False
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass  # 'file' not available, skip arch check

        # Check functionality
        try:
            subprocess.run(
                [str(binary_path)] + check_args,
                capture_output=True,
                timeout=10,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _download_models(self) -> None:
        """Download all required models."""
        log.info("Downloading models...")
        
        for model_name, config in MODELS.items():
            self._download_model(model_name, config)

    def _download_model(self, name: str, config: dict) -> None:
        """Download a single model."""
        model_dir = config["dir"]
        model_path = model_dir / config["file"]
        
        # Create directory
        model_dir.mkdir(parents=True, exist_ok=True)
        
        # Check if model exists
        if not self.force and model_path.exists():
            size_mb = model_path.stat().st_size / (1024 * 1024)
            log.info(f"{name} already exists ({size_mb:.1f}MB)")
            return
        
        log.info(f"Downloading {config['description']}...")
        
        try:
            urllib.request.urlretrieve(config["url"], model_path)
            size_mb = model_path.stat().st_size / (1024 * 1024)
            log.info(f"Downloaded {name} ({size_mb:.1f}MB)")
        except Exception as e:
            log.error(f"Failed to download {name}: {e}")

    def _print_status(self) -> None:
        """Print system status."""
        print()
        print("=" * 60)
        print("  System Status")
        print("=" * 60)
        
        # Python
        print(f"  Python: {sys.version_info.major}.{sys.version_info.minor}")
        
        # Virtual environment
        in_venv = self._is_in_venv()
        print(f"  Virtual Environment: {'Active' if in_venv else 'Not Active'}")
        
        # Binaries
        print()
        print("  Binaries:")
        for name in BINARIES:
            path = self._get_binary_path(name)
            status = "✓" if path.exists() else "✗"
            print(f"    {status} {name}")
        
        # Models
        print()
        print("  Models:")
        for name, config in MODELS.items():
            path = config["dir"] / config["file"]
            if path.exists():
                size_mb = path.stat().st_size / (1024 * 1024)
                print(f"    ✓ {name} ({size_mb:.1f}MB)")
            else:
                print(f"    ✗ {name} (not downloaded)")
        
        print()
        print("=" * 60)


# ============================================================================
# CLI Entry Point
# ============================================================================

def main() -> None:
    """Main entry point for MBG CLI."""
    parser = argparse.ArgumentParser(
        prog="mbg",
        description="MBG: Master Bootstrap Guardian - Automated Build & Compatibility System"
    )
    
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check system status only"
    )
    
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force rebuild/re-download everything"
    )
    
    parser.add_argument(
        "--models",
        action="store_true",
        help="Download models only"
    )
    
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build binaries only"
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version=f"MBG v{MBG_VERSION}"
    )
    
    args = parser.parse_args()
    
    # Create MBG instance
    mbg = MasterBootstrapGuardian(force=args.force, check_only=args.check)
    
    # Handle specific commands
    if args.models:
        mbg._download_models()
    elif args.build:
        mbg._build_binaries()
    else:
        mbg.run()


if __name__ == "__main__":
    main()
