"""
Sorachio-STS Server Manager
Manages llama-server subprocess lifecycle for both LLM instances.

Handles:
  - Starting llama-server processes
  - Health monitoring
  - Graceful shutdown
  - Log capture from server processes
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from pathlib import Path

from config.settings import LLMInstanceConfig
from utils.logging_setup import get_logger

log = get_logger("services.server_manager")


# ---------------------------------------------------------------------------
# SingleServerManager
# ---------------------------------------------------------------------------

class SingleServerManager:
    """
    Manages a single llama-server instance.
    """

    def __init__(
        self,
        name: str,
        binary_path: Path,
        model_path: Path,
        port: int,
        config: LLMInstanceConfig,
        log_dir: Path,
    ):
        self.name = name
        self.binary_path = binary_path
        self.model_path = model_path
        self.port = port
        self.config = config
        self.log_dir = log_dir
        self._process: subprocess.Popen | None = None
        self._log_file = None

    def _build_command(self) -> list[str]:
        cmd = [
            str(self.binary_path),
            "--model", str(self.model_path),
            "--port", str(self.port),
            "--ctx-size", str(self.config.n_ctx),
            "--threads", str(self.config.n_threads),
            "--n-gpu-layers", str(self.config.n_gpu_layers),
            "--host", "127.0.0.1",
            "--log-disable",       # suppress verbose server logs to console
            "--no-mmap",
        ]
        return cmd

    async def start(self) -> bool:
        """Start the server. Returns True if started successfully."""
        if self._process and self._process.poll() is None:
            log.info(f"[{self.name}] Already running (PID {self._process.pid})")
            return True

        binary = self.binary_path
        model = self.model_path

        if not binary.exists():
            log.error(f"[{self.name}] Binary not found: {binary}")
            log.error("Run 'python mbg.py' to auto-build llama-server.")
            return False

        if not model.exists():
            log.error(f"[{self.name}] Model not found: {model}")
            return False

        cmd = self._build_command()
        log.info(f"[{self.name}] Starting on port {self.port}")
        log.debug(f"[{self.name}] Command: {' '.join(cmd)}")

        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{self.name.lower().replace(' ', '_')}_server.log"
        self._log_file = open(log_path, "w", encoding="utf-8")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_file,
                stderr=self._log_file,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                if os.name == "nt"
                else 0,
            )
            log.info(f"[{self.name}] Started (PID {self._process.pid}) → log: {log_path}")
            return True
        except Exception as e:
            log.error(f"[{self.name}] Failed to start: {e}")
            return False

    def stop(self) -> None:
        """Gracefully stop the server."""
        if self._process:
            if self._process.poll() is None:
                log.info(f"[{self.name}] Stopping (PID {self._process.pid})")
                try:
                    if os.name == "nt":
                        self._process.terminate()
                    else:
                        self._process.send_signal(signal.SIGTERM)
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    log.warning(f"[{self.name}] Force killing server")
                    self._process.kill()
                except Exception as e:
                    log.error(f"[{self.name}] Error stopping: {e}")
            self._process = None

        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None


# ---------------------------------------------------------------------------
# ServerManager (orchestrates both LLM servers)
# ---------------------------------------------------------------------------

class ServerManager:
    """
    Orchestrates both llama-server instances for:
      - LLM #1: Cognitive Gateway
      - LLM #2: Personality Core
    """

    def __init__(self, llm_config, project_root: Path):
        self.project_root = project_root
        self.llm_config = llm_config

        binary = project_root / llm_config.server_binary
        log_dir = project_root / "logs"

        self._servers: dict[str, SingleServerManager] = {
            "cognitive_gateway": SingleServerManager(
                name="CognitiveGateway",
                binary_path=binary,
                model_path=project_root / llm_config.cognitive_gateway.model_path,
                port=llm_config.cognitive_gateway.server_port,
                config=llm_config.cognitive_gateway,
                log_dir=log_dir,
            ),
            "personality_core": SingleServerManager(
                name="PersonalityCore",
                binary_path=binary,
                model_path=project_root / llm_config.personality_core.model_path,
                port=llm_config.personality_core.server_port,
                config=llm_config.personality_core,
                log_dir=log_dir,
            ),
        }

    async def start_all(self, wait_ready: bool = True) -> bool:
        """Start all servers. Returns True if all started."""
        results = []
        for name, srv in self._servers.items():
            ok = await srv.start()
            results.append(ok)

        if not all(results):
            return False

        if wait_ready:
            from llm.llama_client import LlamaClient
            cfg_gw = self.llm_config.cognitive_gateway
            cfg_pc = self.llm_config.personality_core

            clients = [
                LlamaClient(cfg_gw.server_url, timeout_s=cfg_gw.timeout_s),
                LlamaClient(cfg_pc.server_url, timeout_s=cfg_pc.timeout_s),
            ]
            names = ["CognitiveGateway", "PersonalityCore"]

            log.info("Waiting for servers to be ready...")
            tasks = [
                asyncio.create_task(c.wait_for_ready(timeout_s=90.0))
                for c in clients
            ]
            readiness = await asyncio.gather(*tasks)

            for n, ready in zip(names, readiness):
                if ready:
                    log.info(f"[OK] {n} is ready")
                else:
                    log.error(f"[FAIL] {n} failed to become ready")

            for c in clients:
                await c.close()

            return all(readiness)

        return True

    def stop_all(self) -> None:
        """Stop all servers gracefully."""
        for srv in self._servers.values():
            srv.stop()

    def status(self) -> dict[str, bool]:
        return {name: srv.is_running() for name, srv in self._servers.items()}
