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
import threading
import urllib.request
from pathlib import Path

# Force UTF-8 encoding for standard output/error on Windows to prevent encoding crashes
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

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

# Force HF_HOME to point to models/tts/kokoro BEFORE any huggingface_hub import
if "HF_HOME" not in os.environ:
    os.environ["HF_HOME"] = str(MODELS_DIR / "tts" / "kokoro")

# Model configurations (only STT is auto-downloaded; LLM models are user-managed)
MODELS = {}

# LLM model directories (auto-detected, user-managed)
LLM_MODEL_DIRS = {
    "llm1": {
        "dir": MODELS_DIR / "llm1",
        "label": "Cognitive Gateway",
    },
    "llm2": {
        "dir": MODELS_DIR / "llm2",
        "label": "Personality Core",
    },
}

# Binary configurations
BINARIES = {
    "llama-server": {
        "repo": "llama.cpp",
        "url": "https://github.com/ggerganov/llama.cpp",
        "build_args": [
            "-DLLAMA_BUILD_SERVER=ON",
            "-DGGML_AVX2=ON",
            "-DGGML_FMA=ON",
            "-DGGML_F16C=ON",
        ],
        "check_args": ["--version"],
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
        # 1. Check Python version (silent — only warns/relaunches if bad)
        self._check_python_version()

        if self.check_only:
            self._print_banner()
            self._print_status()
            return

        # 2. Setup virtual environment (may re-exec into venv)
        self._setup_venv()

        # 2.5 Configure audio environment (WSL / PulseAudio)
        self._setup_audio_environment()

        # ── Fast path: everything already ready ──────────────────────
        if not self.force and self._is_all_ready():
            self._print_status_compact()
            return

        # ── Slow path: run full bootstrap ────────────────────────────
        self._print_banner()

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

    def _print_status_compact(self) -> None:
        """Print a compact one-line status when everything is already ready."""
        parts = []
        # Python
        parts.append(f"Python {sys.version_info.major}.{sys.version_info.minor}")
        # Venv
        parts.append("venv [OK]")
        # Binaries
        for name in BINARIES:
            path = self._get_binary_path(name)
            parts.append(f"{name} [OK]" if path.exists() else f"{name} [FAIL]")
        # Models
        stt_dir = MODELS_DIR / "stt"
        parts.append("STT [OK]" if stt_dir.exists() and any(stt_dir.iterdir()) else "STT [FAIL]")
        piper_model = MODELS_DIR / "tts" / "id_ID-news_tts-medium.onnx"
        parts.append("Piper [OK]" if piper_model.exists() else "Piper [FAIL]")
        kokoro_dir = MODELS_DIR / "tts" / "kokoro"
        kokoro_ok = kokoro_dir.exists() and any(kokoro_dir.rglob("*.pth"))
        parts.append("Kokoro [OK]" if kokoro_ok else "Kokoro [FAIL]")
        # LLM models (auto-detected)
        for name, config in LLM_MODEL_DIRS.items():
            model_dir = config["dir"]
            gguf_files = list(model_dir.glob("*.gguf")) if model_dir.exists() else []
            main_models = [f for f in gguf_files if "mmproj" not in f.name.lower()]
            if main_models:
                model_file = max(main_models, key=lambda f: f.stat().st_size)
                size_mb = model_file.stat().st_size / (1024 * 1024)
                vision = " +vision" if any("mmproj" in f.name.lower() for f in gguf_files) else ""
                parts.append(f"{name} [OK] {model_file.stem} ({size_mb:.0f}MB{vision})")
            else:
                parts.append(f"{name} [MISSING]")
        print(f"[MBG] [OK] System ready | {' | '.join(parts)}")

    def _check_python_version(self) -> None:
        """Check if Python version is compatible."""
        major, minor = sys.version_info[:2]

        if major != 3 or not (PYTHON_MIN[1] <= minor <= PYTHON_MAX[1]):
            log.warning(
                f"Python {major}.{minor} is outside compatible range "
                f"({PYTHON_MIN[0]}.{PYTHON_MIN[1]} - {PYTHON_MAX[0]}.{PYTHON_MAX[1]})"
            )
            self._relaunch_with_compatible_python()

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
                    try:
                        subprocess.run([exe_path] + sys.argv)
                    except KeyboardInterrupt:
                        pass
                    sys.exit(0)

        log.error("No compatible Python version found!")
        sys.exit(1)

    def _is_all_ready(self) -> bool:
        """Fast check: is the entire system already bootstrapped?"""
        # Must be in venv
        if not self._is_in_venv():
            return False

        # Dependencies installed?
        if not self._are_dependencies_installed():
            return False

        # All binaries valid?
        for name, config in BINARIES.items():
            path = self._get_binary_path(name)
            if not self._is_binary_valid(path, config["check_args"]):
                return False

        # STT model present in models/stt?
        stt_dir = MODELS_DIR / "stt"
        if not stt_dir.exists() or not any(stt_dir.iterdir()):
            return False

        # TTS Piper model present in models/tts?
        piper_model = MODELS_DIR / "tts" / "id_ID-news_tts-medium.onnx"
        if not piper_model.exists():
            return False

        # TTS Kokoro model present in models/tts/kokoro?
        kokoro_dir = MODELS_DIR / "tts" / "kokoro"
        if not kokoro_dir.exists() or not any(kokoro_dir.rglob("*.pth")):
            return False

        # LLM model directories have .gguf files?
        for _name, config in LLM_MODEL_DIRS.items():
            model_dir = config["dir"]
            if not model_dir.exists():
                return False
            gguf_files = [f for f in model_dir.glob("*.gguf") if "mmproj" not in f.name.lower()]
            if not gguf_files:
                return False

        return True

    def _are_dependencies_installed(self) -> bool:
        """Quick check: can we import critical packages and find system libs?"""
        critical_packages = [
            "httpx", "aiohttp", "pydantic", "sounddevice",
            "numpy", "rich", "typer", "faster_whisper", "piper",
            "kokoro", "misaki", "langdetect", "cv2", "PIL",
        ]
        for pkg in critical_packages:
            try:
                __import__(pkg)
            except (ImportError, OSError):
                return False

        if sys.platform.startswith("linux"):
            import ctypes.util
            if not ctypes.util.find_library("portaudio"):
                return False

        return True

    def _setup_venv(self) -> None:
        """Create and activate virtual environment."""
        if self._is_in_venv():
            return

        log.info("Setting up virtual environment...")

        # Get venv Python path
        if os.name == "nt":
            venv_python = VENV_DIR / "Scripts" / "python.exe"
        else:
            venv_python = VENV_DIR / "bin" / "python"

        if not venv_python.exists():
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
        try:
            subprocess.run([str(venv_python)] + sys.argv)
        except KeyboardInterrupt:
            pass
        sys.exit(0)

    def _is_in_venv(self) -> bool:
        """Check if running inside a virtual environment."""
        return sys.prefix != sys.base_prefix

    def _install_system_libraries(self) -> None:
        """Install system-level C libraries required by Python packages."""
        if not sys.platform.startswith("linux"):
            return  # Windows/macOS bundle these or handle differently

        # Map: package_manager -> list of packages to install
        # Includes PortAudio, libsndfile, PulseAudio (backend for PortAudio),
        # ALSA libs/plugins so audio works on Linux/WSL, and Vulkan/SPIR-V
        # development libraries so llama-server compiles with GPU acceleration.
        sys_deps: dict[str, list[str]] = {
            "apt-get": [
                "libportaudio2", "portaudio19-dev", "libsndfile1",
                "pulseaudio", "libpulse-dev", "libasound2-dev", "libasound2-plugins",
                "libvulkan-dev", "vulkan-tools", "spirv-headers", "glslang-tools", "shaderc",
                "espeak-ng", "libespeak-ng-dev"
            ],
            "dnf": [
                "portaudio", "portaudio-devel", "libsndfile",
                "pulseaudio", "pulseaudio-libs-devel", "alsa-lib-devel", "alsa-plugins-pulseaudio",
                "vulkan-devel", "vulkan-headers", "spirv-headers-devel", "spirv-tools", "glslc", "glslang",
                "espeak-ng", "espeak-ng-devel"
            ],
            "yum": [
                "portaudio", "portaudio-devel", "libsndfile",
                "pulseaudio", "pulseaudio-libs-devel", "alsa-lib-devel", "alsa-plugins-pulseaudio",
                "vulkan-devel", "vulkan-headers", "spirv-headers-devel", "spirv-tools", "glslc", "glslang",
                "espeak-ng", "espeak-ng-devel"
            ],
            "pacman": [
                "portaudio", "libsndfile",
                "pulseaudio", "alsa-lib", "pulseaudio-alsa",
                "vulkan-devel", "spirv-headers", "spirv-tools", "shaderc",
                "espeak-ng"
            ],
            "zypper": [
                "portaudio", "portaudio-devel", "libsndfile",
                "pulseaudio", "alsa-devel", "alsa-plugins-pulse",
                "vulkan-devel", "vulkan-headers", "spirv-headers-devel", "spirv-tools", "shaderc",
                "espeak-ng", "espeak-ng-devel"
            ],
            "apk": [
                "portaudio-dev", "libsndfile-dev",
                "pulseaudio-dev", "alsa-lib-dev", "alsa-plugins-pulse",
                "vulkan-headers", "shaderc",
                "espeak-ng"
            ],
        }

        for pm_name, packages in sys_deps.items():
            if shutil.which(pm_name):
                log.info(f"Installing system libraries via {pm_name}...")
                if pm_name == "pacman":
                    cmd = ["sudo", pm_name, "-S", "--noconfirm"] + packages
                elif pm_name == "apk":
                    cmd = ["sudo", pm_name, "add"] + packages
                else:
                    cmd = ["sudo", pm_name, "install", "-y"] + packages
                subprocess.run(cmd, check=False)
                return

        log.warning("No supported package manager found — system libraries may be missing")

    # ── WSL / Audio environment setup ────────────────────────────

    @staticmethod
    def _is_wsl() -> bool:
        """Detect if running inside Windows Subsystem for Linux."""
        if not sys.platform.startswith("linux"):
            return False
        try:
            with open("/proc/version") as f:
                return "microsoft" in f.read().lower()
        except OSError:
            return False

    def _setup_audio_environment(self) -> None:
        """
        Configure audio environment for the current platform.

        On WSL this sets PULSE_SERVER so PortAudio → PulseAudio → Windows
        audio pipeline works. Three strategies are tried in order:
          1. WSLg socket  (/mnt/wslg/PulseServer)
          2. User-set PULSE_SERVER (keep as-is)
          3. TCP fallback (localhost via Windows-side PulseAudio)
        """
        if not self._is_wsl():
            return  # Native Linux / Windows / macOS — no special setup needed

        # Already set by user or previous run?
        if os.environ.get("PULSE_SERVER"):
            log.info(f"[Audio] PULSE_SERVER already set: {os.environ['PULSE_SERVER']}")
            return

        # ── Strategy 1: WSLg (Windows 11 22H2+) ──────────────────
        wslg_socket = Path("/mnt/wslg/PulseServer")
        if wslg_socket.exists():
            pulse_addr = f"unix:{wslg_socket}"
            os.environ["PULSE_SERVER"] = pulse_addr
            log.info(f"[Audio] WSLg detected — PULSE_SERVER={pulse_addr}")
            return

        # ── Strategy 2: TCP fallback (manual PulseAudio on Windows) ─
        # Common setup: PulseAudio server on Windows listening on TCP
        tcp_addr = "tcp:127.0.0.1:4713"
        os.environ["PULSE_SERVER"] = tcp_addr
        log.warning(
            f"[Audio] WSL detected but no WSLg socket found. "
            f"Set PULSE_SERVER={tcp_addr} (requires PulseAudio on Windows side). "
            f"For best results, use Windows 11 with WSLg enabled."
        )

    # ── Spinner helper (no external deps needed) ──────────────────

    @staticmethod
    def _spinner_loop(
        stop_event: threading.Event,
        message_func,
    ) -> None:
        """Background thread: render a braille-dot spinner on the same line."""
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        idx = 0
        while not stop_event.is_set():
            msg = message_func()
            sys.stdout.write(f"\r  {frames[idx]} {msg}")
            sys.stdout.flush()
            idx = (idx + 1) % len(frames)
            stop_event.wait(0.08)
        # Clear the spinner line when done
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()

    def _pip_install_one(
        self,
        pkg: str,
        idx: int,
        total: int,
    ) -> bool:
        """Install a single pip package with a live spinner."""
        label = pkg.split(">=")[0].split("[")[0]  # display name without version spec
        status_line = f"[{idx}/{total}] Installing {label}..."

        stop = threading.Event()
        thread = threading.Thread(
            target=self._spinner_loop,
            args=(stop, lambda: status_line),
            daemon=True,
        )
        thread.start()

        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", pkg],
                capture_output=True,
                check=True,
            )
            stop.set()
            thread.join()
            log.info(f"[{idx}/{total}] ✓ {label}")
            return True
        except subprocess.CalledProcessError:
            stop.set()
            thread.join()
            log.warning(f"[{idx}/{total}] ✗ {label} — install failed")
            return False

    # ── Dependency installation ──────────────────────────────────

    def _install_dependencies(self) -> None:
        """Install required Python packages with per-package progress."""
        if not self.force and self._are_dependencies_installed():
            log.info("Dependencies already installed, skipping")
            return

        log.info("Installing dependencies...")

        # Install system-level C libraries first (PortAudio, libsndfile, etc.)
        self._install_system_libraries()

        # Upgrade pip first (silent)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
            capture_output=True,
            check=True,
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
            "faster-whisper",
            "piper-tts",
            "kokoro",
            "misaki[en]",
            "langdetect",
            "opencv-python",
            "Pillow",
        ]

        # VAD package (try binary wheel first)
        vad_pkg = "webrtcvad-wheels"
        all_deps = deps + [vad_pkg]
        total = len(all_deps)

        log.info(f"Installing {total} packages...")
        print()  # blank line before progress

        failed: list[str] = []
        for i, pkg in enumerate(all_deps, 1):
            ok = self._pip_install_one(pkg, i, total)

            # webrtcvad-wheels failed → fallback to source build
            if not ok and pkg == "webrtcvad-wheels":
                log.info("  ↳ Falling back to webrtcvad (source build)...")
                ok = self._pip_install_one("webrtcvad", i, total)

            if not ok:
                failed.append(pkg)

        print()  # blank line after progress

        if failed:
            log.warning(f"Some packages failed to install: {', '.join(failed)}")
        else:
            log.info("All dependencies installed successfully")

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
            if shutil.which("brew"):
                subprocess.run(["brew", "install", tool], check=False)
            else:
                log.warning("Homebrew not found — cannot auto-install on macOS")

        elif sys.platform.startswith("linux"):
            # Detect available package manager (covers Debian, Fedora, Arch, SUSE, Alpine, etc.)
            pkg_managers = [
                (["apt-get", "install", "-y", tool], "apt-get"),
                (["dnf", "install", "-y", tool], "dnf"),
                (["yum", "install", "-y", tool], "yum"),
                (["pacman", "-S", "--noconfirm", tool], "pacman"),
                (["zypper", "install", "-y", tool], "zypper"),
                (["apk", "add", tool], "apk"),
            ]

            for cmd, pm_name in pkg_managers:
                if shutil.which(pm_name):
                    log.info(f"Using package manager: {pm_name}")
                    subprocess.run(["sudo"] + cmd, check=False)
                    break
            else:
                log.warning("No supported package manager found (tried apt-get, dnf, yum, pacman, zypper, apk)")

        else:
            log.warning(f"Cannot auto-install {tool} on {sys.platform}")

        # Verify the tool is actually available after install attempt
        if not shutil.which(tool):
            log.error(
                f"Build tool '{tool}' is still not available after install attempt. "
                f"Please install '{tool}' manually and re-run MBG."
            )
            sys.exit(1)

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
        build_args = list(config["build_args"])
        if name == "llama-server":
            # Auto-detect Vulkan capability on target machine
            has_vulkan = False
            if shutil.which("vulkaninfo"):
                has_vulkan = True
            elif Path("/usr/include/vulkan/vulkan.h").exists() or Path("/usr/local/include/vulkan/vulkan.h").exists():
                has_vulkan = True
            elif sys.platform == "win32" and os.environ.get("VULKAN_SDK"):
                has_vulkan = True

            if has_vulkan:
                log.info("[MBG] Vulkan support detected on host. Enabling Vulkan GPU backend...")
                if "-DGGML_VULKAN=ON" not in build_args:
                    build_args.append("-DGGML_VULKAN=ON")
            else:
                log.info("[MBG] No Vulkan SDK or GPU tools detected. Reverting to optimized CPU build...")
                build_args = [arg for arg in build_args if "GGML_VULKAN" not in arg]

        cmake_args = ["cmake", "-B", str(build_dir)] + build_args
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

            # Copy all backend shared libraries (.so / .dll / .dylib) so Vulkan/CPU backends load at runtime
            src_dir = src_bin.parent
            for lib_pattern in ("*.so*", "*.dll", "*.dylib"):
                for lib_file in src_dir.glob(lib_pattern):
                    if lib_file.is_file():
                        shutil.copy(lib_file, BIN_DIR / lib_file.name)

            # On Linux, apply cap_ipc_lock so llama-server can mlock() model weights
            # without root (prevents model swapping under memory pressure)
            if name == "llama-server" and os.name != "nt":
                try:
                    result = subprocess.run(
                        ["sudo", "setcap", "cap_ipc_lock=+ep", str(binary_path)],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0:
                        log.info(f"{name}: cap_ipc_lock capability set (mlock enabled)")
                    else:
                        log.warning(
                            f"{name}: could not set cap_ipc_lock — "
                            f"mlock may fail "
                            f"(run: sudo setcap cap_ipc_lock=+ep {binary_path})"
                        )
                except Exception as e:
                    log.warning(f"{name}: setcap failed: {e}")
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
                if result.returncode == 0:
                    arch = self.current_arch
                    if arch == "x86_64":
                        arch = "x86-64"
                    elif arch == "aarch64":
                        arch = "aarch64"

                    if arch not in result.stdout and self.current_arch not in result.stdout:
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
        """Ensure STT, TTS, and LLM model dependencies are fully downloaded upfront."""
        log.info("Checking and downloading models...")

        if not self._are_dependencies_installed():
            log.info("[MBG] Installing dependencies before model verification...")
            self._install_dependencies()

        # 1. Verify LLM model directories (user-managed, auto-detected)
        for name, config in LLM_MODEL_DIRS.items():
            self._verify_llm_model_dir(name, config)

        # 2. Pre-download STT Whisper model (small / base) to models/stt/
        try:
            stt_model_name = "small"
            yaml_path = PROJECT_ROOT / "config" / "sorachio.yaml"
            if yaml_path.exists():
                import yaml
                with open(yaml_path, encoding="utf-8") as f:
                    cfg_data = yaml.safe_load(f)
                    stt_model_name = cfg_data.get("stt", {}).get("model_size", "small")

            stt_dir = MODELS_DIR / "stt"
            stt_dir.mkdir(parents=True, exist_ok=True)

            log.info(f"[MBG] Verifying Whisper STT model ('{stt_model_name}') in {stt_dir}...")
            from faster_whisper import download_model
            download_model(stt_model_name, output_dir=str(stt_dir))
            log.info(f"[MBG] Whisper STT model ('{stt_model_name}') is ready [OK]")
        except Exception as e:
            log.warning(f"[MBG] STT model verification: {e}")

        # 3. Pre-download Piper TTS voice models to models/tts/
        pip_models_dir = MODELS_DIR / "tts"
        pip_models_dir.mkdir(parents=True, exist_ok=True)

        tts_voices = ["id_ID-news_tts-medium"]
        for voice_name in tts_voices:
            onnx_file = pip_models_dir / f"{voice_name}.onnx"
            json_file = pip_models_dir / f"{voice_name}.onnx.json"
            if not onnx_file.exists() or not json_file.exists():
                log.info(f"[MBG] Pre-downloading TTS voice '{voice_name}'...")
                lang_parts = voice_name.split("-")
                lang_code = lang_parts[0].replace("_", "/")
                voice_code = lang_parts[1]
                quality = lang_parts[2]
                base_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{lang_code}/{voice_code}/{quality}/{voice_name}"
                try:
                    if not onnx_file.exists():
                        urllib.request.urlretrieve(f"{base_url}.onnx", onnx_file)
                    if not json_file.exists():
                        urllib.request.urlretrieve(f"{base_url}.onnx.json", json_file)
                    log.info(f"[MBG] TTS voice '{voice_name}' downloaded successfully [OK]")
                except Exception as e:
                    log.warning(f"[MBG] Failed to download TTS voice '{voice_name}': {e}")
            else:
                log.info(f"[MBG] TTS voice '{voice_name}' is ready [OK]")

        # 4. Pre-download & warm up Kokoro TTS model into models/tts/kokoro/
        try:
            kokoro_dir = MODELS_DIR / "tts" / "kokoro"
            kokoro_dir.mkdir(parents=True, exist_ok=True)
            os.environ["HF_HOME"] = str(kokoro_dir)
            try:
                import huggingface_hub.constants
                huggingface_hub.constants.HF_HOME = str(kokoro_dir)
                huggingface_hub.constants.HF_HUB_CACHE = str(kokoro_dir / "hub")
            except Exception:
                pass

            log.info(f"[MBG] Verifying Kokoro TTS model ('hexgrad/Kokoro-82M') in {kokoro_dir}...")
            from kokoro import KPipeline
            pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
            generator = pipeline("Hello", voice="af_heart", speed=1.0, split_pattern=None)
            for res in generator:
                _ = res[-1]
                break
            log.info("[MBG] Kokoro TTS model ('hexgrad/Kokoro-82M') is ready [OK]")
        except Exception as e:
            log.warning(f"[MBG] Kokoro TTS model verification failed: {e}")

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

    def _verify_llm_model_dir(self, name: str, config: dict) -> None:
        """Verify that a LLM model directory contains .gguf files."""
        model_dir = config["dir"]
        label = config["label"]

        model_dir.mkdir(parents=True, exist_ok=True)

        gguf_files = list(model_dir.glob("*.gguf"))
        main_models = [f for f in gguf_files if "mmproj" not in f.name.lower()]
        mmproj_files = [f for f in gguf_files if "mmproj" in f.name.lower()]

        if main_models:
            model_file = max(main_models, key=lambda f: f.stat().st_size)
            size_mb = model_file.stat().st_size / (1024 * 1024)
            log.info(f"{name} ({label}): {model_file.name} ({size_mb:.0f}MB)")
            if mmproj_files:
                mp = mmproj_files[0]
                mp_size = mp.stat().st_size / (1024 * 1024)
                log.info(f"  └─ Vision projector: {mp.name} ({mp_size:.0f}MB)")
        else:
            log.warning(
                f"{name} ({label}): No .gguf model found in {model_dir}/\n"
                f"  Download a GGUF model and place it in {model_dir}/"
            )

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

        # Models in models/
        print()
        print("  Models (in models/):")

        # STT
        stt_dir = MODELS_DIR / "stt"
        stt_ok = stt_dir.exists() and any(stt_dir.iterdir())
        print(f"    {'✓' if stt_ok else '✗'} STT Model (models/stt/)")

        # Piper TTS
        piper_model = MODELS_DIR / "tts" / "id_ID-news_tts-medium.onnx"
        piper_ok = piper_model.exists()
        print(f"    {'✓' if piper_ok else '✗'} Piper TTS Indonesian (models/tts/id_ID-news_tts-medium.onnx)")

        # Kokoro TTS
        kokoro_dir = MODELS_DIR / "tts" / "kokoro"
        kokoro_ok = kokoro_dir.exists() and any(kokoro_dir.rglob("*.pth"))
        print(f"    {'✓' if kokoro_ok else '✗'} Kokoro TTS English (models/tts/kokoro/)")

        # LLM Models (auto-detected)
        print()
        print("  LLM Models (auto-detected):")
        for name, config in LLM_MODEL_DIRS.items():
            model_dir = config["dir"]
            label = config["label"]
            gguf_files = list(model_dir.glob("*.gguf")) if model_dir.exists() else []
            main_models = [f for f in gguf_files if "mmproj" not in f.name.lower()]
            mmproj_files = [f for f in gguf_files if "mmproj" in f.name.lower()]

            if main_models:
                model_file = max(main_models, key=lambda f: f.stat().st_size)
                size_mb = model_file.stat().st_size / (1024 * 1024)
                vision_tag = " +vision" if mmproj_files else ""
                print(f"    ✓ {name} ({label}): {model_file.name} ({size_mb:.0f}MB{vision_tag})")
                if mmproj_files:
                    mp = mmproj_files[0]
                    mp_size = mp.stat().st_size / (1024 * 1024)
                    print(f"      └─ mmproj: {mp.name} ({mp_size:.0f}MB)")
            else:
                print(f"    ✗ {name} ({label}): No .gguf model found")

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
