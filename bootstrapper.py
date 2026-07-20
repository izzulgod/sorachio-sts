"""
Sorachio-STS Bootstrapper
=========================
Handles environment setup, dependency installation, and external tool builds.
"""

# Use basic logging until rich is installed
import logging
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[Bootstrapper] %(levelname)s: %(message)s")
log = logging.getLogger("bootstrapper")


class Bootstrapper:
    """
    Self-bootstrapping system for Sorachio-STS.
    Ensures the environment is correctly configured before the main application starts.
    """

    def __init__(self):
        self.root = Path(__file__).parent.absolute()
        self.bin_dir = self.root / "bin"
        self.repos_dir = self.root / ".repos"
        self.models_dir = self.root / "models"
        self.tts_models_dir = self.models_dir / "tts"
        self.venv_dir = self.root / "venv_runtime"

    def _check_python_version(self) -> None:
        """Check if the current Python version is within the required range."""
        # Required range: 3.10 <= version < 3.13
        major, minor, micro = sys.version_info.major, sys.version_info.minor, sys.version_info.micro

        if major == 3 and 10 <= minor < 13:
            log.info(f"Python version {major}.{minor}.{micro} is within the compatible range.")
            return

        log.warning(f"Python version {major}.{minor}.{micro} is OUTSIDE the compatible range (3.10 - 3.12)!")
        self._try_relaunch_with_different_python()

    def _try_relaunch_with_different_python(self) -> None:
        """Try to find and relaunch with a compatible Python version."""
        log.info("Searching for compatible Python versions (3.10, 3.11, or 3.12)...")

        for version in ["3.10", "3.11", "3.12"]:
            # Try common executable names
            for exe_name in [f"python{version}", f"python{version.replace('.', '')}"]:
                exe_path = shutil.which(exe_name)
                if exe_path:
                    log.info(f"Found compatible Python: {exe_path}. Relaunching...")
                    # Use subprocess.run instead of os.execv for more reliable relaunch
                    subprocess.run([exe_path] + sys.argv)
                    sys.exit(0)

        log.error("No compatible Python version found in PATH (required: 3.10, 3.11, or 3.12).")
        sys.exit(1)


    def ensure_ready(self) -> None:
        """Main entry point to ensure the system is ready for execution."""
        log.info("Checking system readiness...")

        # 0. Python Version Check
        self._check_python_version()

        # 1. Venv Management
        if not self._is_in_venv():
            log.info("Not running in a virtual environment. Setting up venv_runtime...")
            self._setup_venv()

        # 2. Dependency Installation
        self._install_dependencies()

        # 3. Build External Tools
        self._build_external_tools()

        # 4. Self-Checking (disabled by default for faster startup)
        # self._run_self_checks()

        log.info("System is ready!")

    def _is_in_venv(self) -> bool:
        """Check if the current process is running inside a virtual environment."""
        return sys.prefix != sys.base_prefix

    def _setup_venv(self) -> None:
        """Create a virtual environment and restart the process using it."""
        self.venv_dir.mkdir(parents=True, exist_ok=True)

        # Use subprocess to create venv to ensure we use the current sys.executable
        log.info(f"Creating venv at {self.venv_dir}...")
        subprocess.run([sys.executable, "-m", "venv", str(self.venv_dir)], check=True)

        # Determine the python executable in the venv
        if os.name == "nt":
            python_exe = self.venv_dir / "Scripts" / "python.exe"
        else:
            python_exe = self.venv_dir / "bin" / "python"

        log.info(f"Restarting process with venv python: {python_exe}")

        # Restart the process using subprocess.run to ensure a clean relaunch
        # We pass sys.executable to ensure we are calling the new python
        subprocess.run([str(python_exe)] + sys.argv)
        sys.exit(0)

    def _run_command(
        self,
        cmd: list[str],
        cwd: Path | None = None,
        check: bool = True,
        verbose: bool = False
    ) -> subprocess.CompletedProcess:
        """Helper to run shell commands."""
        try:
            if verbose:
                log.info(f"Executing: {' '.join(cmd)}")
                # For verbose commands, stream output directly to the console
                process = subprocess.Popen(
                    cmd,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                output = []
                for line in process.stdout:
                    print(line, end="")
                    output.append(line)

                return_code = process.wait()
                if check and return_code != 0:
                    raise subprocess.CalledProcessError(return_code, cmd, output="".join(output))
                return subprocess.CompletedProcess(cmd, return_code, stdout="".join(output), stderr="")
            else:
                return subprocess.run(
                    cmd,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    check=check
                )
        except subprocess.CalledProcessError as e:
            log.error(f"Command failed: {' '.join(cmd)}\nError: {e.stderr}")
            if check:
                raise e
            return e

    def _is_binary_valid(self, binary_path: Path, check_args: list[str]) -> bool:
        """Check if a binary exists, is the correct architecture, and is functional."""
        if not binary_path.exists():
            return False

        # 1. Check architecture using 'file' command
        try:
            result = subprocess.run(["file", str(binary_path)], capture_output=True, text=True, check=True)
            current_arch = "arm64" if platform.machine() == "arm64" else "x86_64"
            if current_arch not in result.stdout:
                log.warning(
                    f"Binary {binary_path} architecture mismatch. "
                    f"Expected {current_arch}, found: {result.stdout.strip()}"
                )
                return False
        except subprocess.CalledProcessError as e:
            log.error(f"Failed to check architecture of {binary_path}: {e}")
            return False

        # 2. Check functionality
        try:
            # Run the check command.
            # We use the full path to the binary to avoid issues with cwd.
            subprocess.run([str(binary_path)] + check_args, capture_output=True, text=True, check=True)
            return True
        except subprocess.CalledProcessError as e:
            log.warning(f"Binary {binary_path} failed functionality check ({' '.join(check_args)}): {e.stderr.strip()}")
            return False
        except Exception as e:
            log.warning(f"Unexpected error checking binary {binary_path}: {e}")
            return False

    def _install_dependencies(self) -> None:
        """Install all required Python packages."""
        log.info("Installing dependencies...")

        # Upgrade pip
        self._run_command([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])

        # Core dependencies
        core_deps = [
            "ruff",
            "pyrefly",
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
            "pytest>=8.0.0",
            "pytest-asyncio>=0.23.0",
            "faster-whisper",
            "piper-tts",
            "langdetect",
        ]

        log.info(f"Installing core packages: {', '.join(core_deps)}")
        self._run_command([sys.executable, "-m", "pip", "install"] + core_deps)

        # VAD installation (try wheels first)
        log.info("Installing VAD...")
        try:
            self._run_command([sys.executable, "-m", "pip", "install", "webrtcvad-wheels"])
        except subprocess.CalledProcessError:
            log.info("webrtcvad-wheels failed, trying webrtcvad...")
            self._run_command([sys.executable, "-m", "pip", "install", "webrtcvad"])

    def _install_system_tool(self, tool: str) -> bool:
        """Attempt to install a system tool using the available package manager."""
        log.info(f"Attempting to auto-install missing tool: {tool}...")

        # OS to Package Manager Mapping
        managers = {
            "Darwin": {
                "cmd": "brew",
                "install": ["brew", "install"],
                "check": "brew",
                "packages": {"cmake": "cmake", "git": "git", "make": "make"}
            },
            "Linux": {
                "cmd": "apt-get", # Default to apt, can be extended
                "install": ["sudo", "apt-get", "install", "-y"],
                "check": "apt-get",
                "packages": {"cmake": "cmake", "git": "git", "make": "build-essential"}
            },
            "Windows": {
                "cmd": "winget",
                "install": [
                    "winget",
                    "install",
                    "--silent",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ],
                "check": "winget",
                "packages": {"cmake": "Kitware.CMake", "git": "Git.Git", "make": "ezwinports.make"}
            }
        }

        # Detect OS
        os_type = ""
        if sys.platform == "darwin":
            os_type = "Darwin"
        elif sys.platform.startswith("linux"):
            os_type = "Linux"
        elif sys.platform == "win32":
            os_type = "Windows"

        if not os_type or os_type not in managers:
            log.error(f"Unsupported OS for auto-installation: {sys.platform}")
            return False

        mgr = managers[os_type]

        # Check if manager is installed
        if shutil.which(mgr["cmd"]) is None:
            log.error(f"Package manager {mgr['cmd']} not found. Please install {tool} manually.")
            return False

        # Resolve package name
        pkg_name = mgr["packages"].get(tool, tool)

        try:
            log.info(f"Running {' '.join(mgr['install'])} {pkg_name}...")
            self._run_command(mgr["install"] + [pkg_name])
            log.info(f"Successfully installed {tool} via {mgr['cmd']}.")
            return True
        except Exception as e:
            log.error(f"Failed to install {tool} via {mgr['cmd']}: {e}")
            return False

    def _build_external_tools(self) -> None:
        """Build and install external C++ tools (llama.cpp)."""
        log.info("Building external tools...")

        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self.tts_models_dir.mkdir(parents=True, exist_ok=True)

        # Check and auto-install build tools
        for tool in ["cmake", "git"]:
            if shutil.which(tool) is None:
                if not self._install_system_tool(tool):
                    log.warning(f"Could not auto-install {tool}. Build may fail.")

        # --- llama.cpp ---
        llama_repo = self.repos_dir / "llama.cpp"
        llama_bin = self.bin_dir / "llama-server"
        if not self._is_binary_valid(llama_bin, ["--version"]):
            log.info("Building llama.cpp...")
            if not llama_repo.exists():
                self._run_command(["git", "clone", "https://github.com/ggerganov/llama.cpp", str(llama_repo)])
            else:
                self._run_command(["git", "-C", str(llama_repo), "pull"])

            build_dir = llama_repo / "build"
            self._run_command(["cmake", "-B", str(build_dir), "-DLLAMA_BUILD_SERVER=ON"], cwd=llama_repo)

            # Use all detected CPU threads for building
            threads = os.cpu_count() or 1
            log.info(f"Compiling llama-server using {threads} threads...")
            self._run_command(
                [
                    "cmake",
                    "--build",
                    str(build_dir),
                    "--config",
                    "Release",
                    "-j",
                    str(threads),
                ],
                cwd=llama_repo,
                verbose=True
            )

            # Copy binary (handle Darwin/Linux/Windows)
            # On Darwin/Linux, it's usually in build/bin/llama-server
            src_bin = build_dir / "bin" / "llama-server"
            if src_bin.exists():
                shutil.copy(src_bin, llama_bin)
            else:
                log.warning("Could not find llama-server binary after build.")
        else:
            log.info("llama-server is valid, skipping build.")

        # --- LLM Models (user-managed, auto-detected) ---
        llm1_dir = self.models_dir / "llm1"
        llm2_dir = self.models_dir / "llm2"
        llm1_dir.mkdir(parents=True, exist_ok=True)
        llm2_dir.mkdir(parents=True, exist_ok=True)

        # Check if model directories have .gguf files (user drops them in)
        for name, model_dir in [("LLM1 (Cognitive Gateway)", llm1_dir), ("LLM2 (Personality Core)", llm2_dir)]:
            gguf_files = list(model_dir.glob("*.gguf"))
            main_models = [f for f in gguf_files if "mmproj" not in f.name.lower()]
            if main_models:
                log.info(f"{name}: Found model → {main_models[0].name}")
                mmproj_files = [f for f in gguf_files if "mmproj" in f.name.lower()]
                if mmproj_files:
                    log.info(f"  Vision projector → {mmproj_files[0].name}")
            else:
                log.warning(
                    f"{name}: No .gguf model found in {model_dir}/\n"
                    f"  Download a GGUF model and place it in {model_dir}/"
                )

        # --- STT (faster-whisper) ---
        # faster-whisper auto-downloads CTranslate2 models from Hugging Face.
        # No build step needed.
        log.info("STT: faster-whisper models are auto-downloaded on first use.")

        # --- TTS (Piper) ---
        # Piper ONNX models are auto-downloaded on first use by PiperTTSClient.
        log.info("TTS: Piper voice models are auto-downloaded on first use.")
        log.info(f"  Model directory: {self.tts_models_dir}")

    def _run_self_checks(self) -> None:
        """Run linting and static analysis checks."""
        log.info("Running self-checks...")

        # Ruff check
        try:
            self._run_command([sys.executable, "-m", "ruff", "check", "."])
            log.info("Ruff check passed.")
        except subprocess.CalledProcessError as e:
            log.warning("Ruff found some issues:")
            if e.stdout:
                print(e.stdout)
            if e.stderr:
                print(e.stderr)

        # Pyrefly check
        try:
            # Run pyrefly with --project-excludes to exclude venv_runtime
            self._run_command([
                "pyrefly", "check", ".",
                "--project-excludes",
                "venv_runtime,venv,bin,.repos,.ruff_cache,.pyrefly,logs,data,models"
            ])
            log.info("Pyrefly check passed.")
        except subprocess.CalledProcessError as e:
            log.warning("Pyrefly found some issues:")
            if e.stdout:
                print(e.stdout)
            if e.stderr:
                print(e.stderr)
        except FileNotFoundError:
            log.warning("Pyrefly check failed: tool not found.")
