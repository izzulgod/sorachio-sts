"""
Sorachio-STS CLI
Rich terminal interface for testing, monitoring, and running the companion.

Modes:
  sorachio run             - Full voice mode (microphone + speakers)
  sorachio text            - Text input mode (no microphone needed)
  sorachio test-stt        - Test STT component only
  sorachio test-tts        - Test TTS component only
  sorachio test-cognitive  - Test Cognitive Gateway only
  sorachio servers status  - Show llama-server status
  sorachio servers start   - Start llama-servers
  sorachio servers stop    - Stop llama-servers
  sorachio memory list     - List long-term memories
  sorachio memory clear    - Clear all memories
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import warnings
from pathlib import Path

import typer
from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.panel import Panel
from rich.prompt import Prompt
from rich.spinner import Spinner
from rich.table import Table

# ------------------------------------------------------------------
# Global suppression of unauthenticated HF warnings and PyTorch spam
# ------------------------------------------------------------------
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*words count mismatch.*")

# Silence noisy library loggers immediately (before any import)
for _noisy in (
    "httpx", "kokoro", "urllib3", "whisper", "faster_whisper",
    "cognition.gateway", "phonemizer", "espeak", "numba",
    "huggingface_hub",
):
    logging.getLogger(_noisy).setLevel(logging.ERROR)


class _NoiseFilter(logging.Filter):
    """Drop log records whose message contains known spam strings."""
    _PATTERNS = (
        "words count mismatch",
        "JSON repaired",
        "unauthenticated requests",
        "HF_TOKEN",
        "dropout option adds",
    )
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in self._PATTERNS)


logging.root.addFilter(_NoiseFilter())

# Add project root to path
_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root))

console = Console(
    soft_wrap=True,
    force_terminal=True,
)
app = typer.Typer(
    name="sorachio",
    help="Sorachio-STS: Speech To Speech AI Companion System",
    rich_markup_mode="rich",
    add_completion=False,
)
servers_app = typer.Typer(name="servers", help="Manage llama-server instances")
memory_app = typer.Typer(name="memory", help="Memory management")
app.add_typer(servers_app)
app.add_typer(memory_app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_settings(config: str | None = None):
    from config.settings import load_settings
    try:
        settings = load_settings(config)
        return settings
    except FileNotFoundError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise typer.Exit(1)


def _setup_logging(settings):
    import os

    # ------------------------------------------------------------------
    # Hide annoying warnings globally
    # ------------------------------------------------------------------
    import warnings

    from utils.logging_setup import setup_logging
    warnings.filterwarnings("ignore", category=UserWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", message=".*words count mismatch.*")

    for _noisy in (
        "httpx", "kokoro", "urllib3", "whisper", "faster_whisper",
        "cognition.gateway", "phonemizer", "espeak", "numba",
        "huggingface_hub",
    ):
        logging.getLogger(_noisy).setLevel(logging.ERROR)

    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    root = _project_root
    log_dir = str(root / settings.system.log_dir)

    setup_logging(
        level="ERROR",  # suppress WARNING spam to terminal
        log_dir=log_dir,
    )

    # Rich logging handler — ERROR+ only
    logging.basicConfig(
        level=logging.ERROR,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                markup=True,
                show_path=False,
                show_time=False,
            )
        ],
    )

def _print_banner():
    console.print(Panel.fit(
        "[bold cyan]Sorachio-STS[/bold cyan] [dim]v0.1.0[/dim]\n"
        "[dim]Speech To Speech AI Companion System[/dim]",
        border_style="cyan",
    ))


# ---------------------------------------------------------------------------
# run command
# ---------------------------------------------------------------------------

@app.command()
def run(
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    no_greeting: bool = typer.Option(False, "--no-greeting", help="Skip startup greeting"),
    no_servers: bool = typer.Option(False, "--no-servers", help="Skip starting llama-servers"),
):
    """Run Sorachio in full voice mode (microphone + speakers)."""
    _print_banner()
    settings = _load_settings(config)
    _setup_logging(settings)

    if no_greeting:
        settings.pipeline.startup_greeting = False

    asyncio.run(_run_pipeline(settings, voice_mode=True, no_servers=no_servers))


# ---------------------------------------------------------------------------
# text command
# ---------------------------------------------------------------------------

@app.command()
def text(
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    message: str | None = typer.Option(None, "--message", "-m", help="Single message (non-interactive)"),
    no_servers: bool = typer.Option(False, "--no-servers", help="Skip starting llama-servers"),
):
    """Run Sorachio in text input mode (no microphone required)."""
    _print_banner()
    settings = _load_settings(config)
    _setup_logging(settings)
    asyncio.run(_run_text_mode(settings, single_message=message, no_servers=no_servers))

async def _run_text_mode(settings, single_message=None, no_servers=False):
    import logging
    import warnings

    from core.pipeline import SorachioPipeline
    from services.server_manager import ServerManager

    warnings.filterwarnings("ignore", category=UserWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", message=".*words count mismatch.*")
    for _noisy in (
        "httpx", "kokoro", "urllib3", "whisper", "faster_whisper",
        "cognition.gateway", "phonemizer", "espeak", "numba",
        "huggingface_hub",
    ):
        logging.getLogger(_noisy).setLevel(logging.ERROR)

    # ------------------------------------------------------------------
    # Start servers
    # ------------------------------------------------------------------

    root = _project_root
    srv_mgr = None

    if not no_servers:
        console.print("\n[cyan]Starting LLM servers...[/cyan]\n")

        srv_mgr = ServerManager(settings.llm, root)

        with Live(
            Spinner("dots", text="[cyan]Booting models...[/cyan]"),
            console=console,
            refresh_per_second=12,
            transient=True,   # disappears cleanly when done
        ):
            ok = await srv_mgr.start_all(wait_ready=True)

        if not ok:
            console.print("[red]Failed to start LLM servers[/red]")
            return
        console.print("[dim][OK] LLM servers ready[/dim]")

    # ------------------------------------------------------------------
    # Pipeline setup
    # ------------------------------------------------------------------

    pipeline = SorachioPipeline(settings)

    response_ready = asyncio.Event()
    response_ready.set()

    voice_cli = VoiceCLI(mode="text")

    from core.events import EventType, get_bus

    async def _on_response_end_local(event):
        """Unblocks input loop after Sorachio finishes responding."""
        await asyncio.sleep(0.05)
        voice_cli.stop()
        response_ready.set()

    async def _on_cognitive_local(event):
        """Unblocks input loop immediately when the AI decides NOT to respond.
        Without this, response_ready.wait() would hang for the full 120-s timeout."""
        decision = event.data
        if not decision.get("respond", True):
            await asyncio.sleep(0.05)
            voice_cli.stop()
            response_ready.set()

    get_bus().subscribe(EventType.RESPONSE_END,    _on_response_end_local)
    get_bus().subscribe(EventType.COGNITIVE_RESULT, _on_cognitive_local)

    settings.pipeline.startup_greeting = False

    console.print("\n[cyan]Initializing pipeline...[/cyan]")

    with Live(
        Spinner("dots", text="[cyan]Loading components...[/cyan]"),
        console=console,
        refresh_per_second=12,
        transient=True,   # disappears cleanly when done
    ):
        ok = await pipeline.setup()

    if not ok:
        console.print("[red]Pipeline setup failed[/red]")
        return
    console.print("[dim][OK] Pipeline ready[/dim]")

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    tasks = [
        asyncio.create_task(
            pipeline._cognitive_worker(),
            name="CognitiveWorker",
        ),
        asyncio.create_task(
            pipeline._tts_worker(),
            name="TTSWorker",
        ),
        asyncio.create_task(
            pipeline._playback.run(),
            name="PlaybackWorker",
        ),
    ]

    # ------------------------------------------------------------------
    # READY SCREEN
    # ------------------------------------------------------------------

    console.print()
    console.rule("[bold green]SORACHIO READY")
    console.print(
        "[green]Text mode active[/green] • "
        "[dim]type 'quit' to exit[/dim]"
    )
    console.print()

    # ------------------------------------------------------------------
    # Single message mode
    # ------------------------------------------------------------------

    if single_message:
        console.print(f"\n[bold cyan]You[/bold cyan]\n└─ {single_message}\n")
        voice_cli.start()
        response_ready.clear()
        await get_bus().emit(EventType.STT_RESULT, data=single_message, source="cli")
        await pipeline.inject_text(single_message)
        await response_ready.wait()

    # ------------------------------------------------------------------
    # Interactive mode
    # ------------------------------------------------------------------

    else:
        while True:
            try:
                # Ask without Live running
                console.print("\n[bold cyan]You[/bold cyan]")
                user_input = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: input("└─ ")
                )

                user_input = user_input.strip()

                if user_input.lower() in ("quit", "exit", "q"):
                    break

                if not user_input:
                    continue

                response_ready.clear()
                voice_cli.start()

                await get_bus().emit(EventType.STT_RESULT, data=user_input, source="cli")
                await pipeline.inject_text(user_input)

                try:
                    await asyncio.wait_for(response_ready.wait(), timeout=120)
                except asyncio.TimeoutError:
                    voice_cli.stop()
                    print(
                        "\033[2m(no response after 120s — you can type again)\033[0m\n",
                        flush=True,
                    )

            except (KeyboardInterrupt, EOFError):
                break

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    console.print("\n[yellow]Shutting down...[/yellow]\n")

    for t in tasks:
        t.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)

    if srv_mgr:
        srv_mgr.stop_all()

    console.rule("[bold red]GOODBYE")

class VoiceCLI:
    """
    Terminal UI manager for Sorachio.

    Spinner lifecycle per turn
    ──────────────────────────
    text mode:  [Thinking…]  → stop → print status bar → print response
    run  mode:  [Listening…] → [Thinking…] → stop → print status bar
                             → print response → restart [Listening…]

    Rule: ALWAYS stop Live before calling console.print(), then restart
    if a new spinner phase is needed. This prevents the freeze artefact.
    """

    _EMOTION_ICON: dict = {
        "neutral":    ("○",  "bright_black"),
        "happy":      ("◕",  "yellow"),
        "sad":        ("◔",  "blue"),
        "anxious":    ("◎",  "magenta"),
        "frustrated": ("◉",  "red"),
        "excited":    ("★",  "bright_yellow"),
        "confused":   ("◈",  "cyan"),
        "tired":      ("◑",  "bright_black"),
    }

    def __init__(self, mode: str = "run"):
        from core.events import get_bus
        self.mode         = mode
        self.response_text = ""
        self._live: Live | None = None
        self.bus          = get_bus()

    # ── spinner helpers ───────────────────────────────────────────────

    def _spin_start(self, label: str, color: str = "yellow") -> None:
        """Start a fresh transient Live spinner. Stops any existing one first."""
        self._spin_stop()
        self._live = Live(
            Spinner("line", text=f"[{color}]{label}[/{color}]", style=color),
            console=console,
            refresh_per_second=14,
            transient=True,   # clears itself completely when stopped
        )
        self._live.start()

    def _spin_stop(self) -> None:
        """Stop and discard the current spinner (transient removes it from screen)."""
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None

    def _spin_label(self, label: str, color: str = "yellow") -> None:
        """Update label of the running spinner without restarting."""
        if self._live is not None:
            self._live.update(
                Spinner("line", text=f"[{color}]{label}[/{color}]", style=color)
            )

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        from core.events import EventType
        if self.mode == "run":
            self._spin_start("Listening…", "cyan")
            self.bus.subscribe(EventType.USER_SPEECH_START, self.on_speech_start)
        else:
            self._spin_start("Thinking…", "yellow")
        self.bus.subscribe(EventType.STT_RESULT,      self.on_stt)
        self.bus.subscribe(EventType.COGNITIVE_RESULT, self.on_cognitive)
        self.bus.subscribe(EventType.RESPONSE_START,  self.on_response_start)
        self.bus.subscribe(EventType.RESPONSE_TOKEN,  self.on_token)
        self.bus.subscribe(EventType.RESPONSE_END,    self.on_response_end)
        self.bus.subscribe(EventType.INTERRUPT,       self.on_interrupt)

    def stop(self) -> None:
        from core.events import EventType
        self._spin_stop()
        if self.mode == "run":
            self.bus.unsubscribe(EventType.USER_SPEECH_START, self.on_speech_start)
        self.bus.unsubscribe(EventType.STT_RESULT,      self.on_stt)
        self.bus.unsubscribe(EventType.COGNITIVE_RESULT, self.on_cognitive)
        self.bus.unsubscribe(EventType.RESPONSE_START,  self.on_response_start)
        self.bus.unsubscribe(EventType.RESPONSE_TOKEN,  self.on_token)
        self.bus.unsubscribe(EventType.RESPONSE_END,    self.on_response_end)
        self.bus.unsubscribe(EventType.INTERRUPT,       self.on_interrupt)

    # ── event handlers ────────────────────────────────────────────────

    async def on_speech_start(self, event) -> None:
        self._spin_label("Listening…", "cyan")

    async def on_stt(self, event) -> None:
        transcript = event.data
        if self.mode == "run":
            # Stop spinner → clean print → restart spinner for thinking
            self._spin_stop()
            console.print(f"\n[bold cyan]You:[/bold cyan] {transcript}")
            self._spin_start("Thinking…", "yellow")
        else:
            self._spin_label("Thinking…", "yellow")

    async def on_cognitive(self, event) -> None:
        # ── Always stop spinner BEFORE printing anything ──────────────
        self._spin_stop()

        decision         = event.data
        emotion          = decision.get("emotion",          "neutral")
        respond          = decision.get("respond",          True)
        memory           = decision.get("store_memory",     False)
        topic            = decision.get("topic",            "general")
        confidence       = decision.get("confidence",       0.5)
        priority         = decision.get("priority",         "medium")
        speech_type      = decision.get("speech_type",      "direct_address")
        social_attention = decision.get("social_attention", 0.5)

        icon, emo_color = self._EMOTION_ICON.get(emotion, ("○", "bright_black"))

        if self.mode == "run":
            # ── Pill/capsule background colors ────────────────────────────
            #   Use Rich's "on <bg>" syntax for the filled-pill look.
            #   Emotion pill
            _EMO_BG = {
                "neutral":    "grey23",
                "happy":      "dark_goldenrod",
                "sad":        "navy_blue",
                "anxious":    "purple4",
                "frustrated": "dark_red",
                "excited":    "dark_orange3",
                "confused":   "dark_cyan",
                "tired":      "grey15",
            }
            emo_bg   = _EMO_BG.get(emotion, "grey23")
            emo_pill = f"[bold white on {emo_bg}] {icon} {emotion} [/]"

            # Respond pill
            if respond:
                r_pill = "[bold white on dark_green] ✓ respond [/]"
            else:
                r_pill = "[bold white on dark_red] ✗ ignore [/]"

            # Memory pill
            if memory:
                m_pill = "[bold white on dark_cyan] ⊛ memory [/]"
            else:
                m_pill = "[dim on grey15]  ○ memory [/]"

            # Topic pill
            t_pill = f"[dim on grey11]  topic: {topic}  [/]"

            # Priority pill
            _PRIORITY_COLORS = {
                "low": "dim",
                "medium": "yellow",
                "high": "bold white on red",
            }
            p_color = _PRIORITY_COLORS.get(priority, "yellow")
            p_pill = f"[{p_color}] ⚡ {priority} [/]"

            # Confidence bar (8 blocks)
            filled   = round(confidence * 8)
            conf_bar = "[green]" + "█" * filled + "[/green]" + "[dim]" + "░" * (8 - filled) + "[/dim]"

            # ── Print the status capsule row ──────────────────────────────
            sep = "  [dim][/dim]  "
            console.print(
                "\n[bold dim]  >>> STATUS[/bold dim]  "
                + emo_pill
                + sep + r_pill
                + sep + p_pill
                + sep + m_pill
                + sep + t_pill
                + sep + f"[dim]conf[/dim] {conf_bar}"
            )
        else:
            # ── Text mode tree layout ─────────────────────────────────────
            # Confidence bar (8 blocks)
            c_filled = round(confidence * 8)
            conf_bar = "█" * c_filled + "░" * (8 - c_filled)
            
            intent_str = "respond" if respond else "ignore"
            mem_str = "true" if memory else "false"
            
            console.print("\n[bold magenta]Cognition[/bold magenta]")
            console.print(f"├─ mood        {emotion}")
            console.print(f"├─ intent      {intent_str}")
            console.print(f"├─ energy      {priority}")
            console.print(f"├─ memory      {mem_str}")
            console.print(f"├─ topic       {topic}")
            console.print(f"└─ confidence  {conf_bar}\n")
            
            if not respond:
                console.print("[dim][ IGNORED ][/dim]")
                console.print("[dim]Low-priority input filtered by cognitive layer.[/dim]\n")
                console.print("────────────────────────────────────────")

        if respond:
            self._spin_start(f"{icon} Composing…", emo_color)
        elif self.mode == "run":
            # In run mode, don't wait for a response that won't come — go back to listening
            self._spin_start("Listening…", "cyan")

    async def on_response_start(self, event) -> None:
        self.response_text = ""
        self._spin_stop()
        if self.mode == "text":
            console.print("[bold green]Sorachio[/bold green]\n└─ ", end="")
        else:
            console.print("\n[bold cyan]Sorachio:[/bold cyan] ", end="")

    async def on_token(self, event) -> None:
        token = event.data
        self.response_text += token
        if self.mode == "text":
            token = token.replace("\n", "\n   ")
        console.print(token, end="", highlight=False)

    async def on_response_end(self, event) -> None:
        console.print()  # Final newline for the response
        if self.mode == "text":
            console.print("\n────────────────────────────────────────")
        elif self.mode == "run":
            self._spin_start("Listening…", "cyan")

    async def on_interrupt(self, event) -> None:
        self._spin_stop()
        console.print("\n[dim]  ╌ Interrupted[/dim]\n")
        if self.mode == "run":
            self._spin_start("Listening…", "cyan")

async def _run_pipeline(settings, voice_mode=True, no_servers=False):
    import platform
    import signal

    from core.pipeline import SorachioPipeline
    from services.server_manager import ServerManager

    root = _project_root
    srv_mgr = None

    if not no_servers:
        console.print("\n[cyan]Starting LLM servers...[/cyan]\n")

        srv_mgr = ServerManager(settings.llm, root)

        with Live(
            Spinner("line", text="[cyan]Booting models...[/cyan]"),
            console=console,
            refresh_per_second=12,
            transient=True,   # disappears cleanly when done
        ):
            ok = await srv_mgr.start_all(wait_ready=True)

        if not ok:
            console.print("[red]Failed to start LLM servers.[/red]")
            console.print("[dim]Hint: Run 'python mbg.py' to auto-build[/dim]")
            return
        console.print("[dim][OK] LLM servers ready[/dim]")

    pipeline = SorachioPipeline(settings)

    console.print("\n[cyan]Initializing pipeline...[/cyan]")

    with Live(
        Spinner("dots", text="[cyan]Loading components...[/cyan]"),
        console=console,
        refresh_per_second=12,
        transient=True,   # disappears cleanly when done
    ):
        ok = await pipeline.setup()

    if not ok:
        console.print("[red][ERROR] Pipeline setup failed[/red]")
        return
    console.print("[dim][OK] Pipeline ready[/dim]")

    # Handle Ctrl+C
    if platform.system() != "Windows":
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, pipeline.request_shutdown)

    console.print("[green][OK] Sorachio is running![/green]")
    console.print("[dim]Speak into your microphone. Press Ctrl+C to stop.[/dim]\n")

    voice_cli = VoiceCLI(mode="run")
    voice_cli.start()

    try:
        await pipeline.run()
    except KeyboardInterrupt:
        pass
    finally:
        voice_cli.stop()
        with Live(
            Spinner("dots", text="[cyan]Shutting down Sorachio…[/cyan]", style="cyan"),
            console=console,
            refresh_per_second=12,
            transient=True,
        ):
            await pipeline.shutdown()
            if srv_mgr:
                srv_mgr.stop_all()
        console.print("[dim][[OK]] Shutdown complete[/dim]")


# ---------------------------------------------------------------------------
# test-stt command
# ---------------------------------------------------------------------------

@app.command("test-stt")
def test_stt(
    config: str | None = typer.Option(None, "--config", "-c"),
    audio_file: str | None = typer.Option(None, "--file", "-f", help="WAV file to transcribe"),
):
    """Test STT component with a WAV file or microphone."""
    settings = _load_settings(config)
    _setup_logging(settings)

    async def _test():
        from stt.whisper_client import WhisperClient
        root = _project_root
        stt = WhisperClient(
            binary_path=str(root / settings.stt.binary_path),
            model_path=str(root / settings.stt.model_path),
        )
        if audio_file:
            import wave
            with wave.open(audio_file, "rb") as wf:
                audio_bytes = wf.readframes(wf.getnframes())
            result = await stt.transcribe(audio_bytes)
            console.print(f"[green]Transcript:[/green] {result!r}")
        else:
            console.print("[yellow]No --file specified. Recording 5 seconds from mic...[/yellow]")
            import sounddevice as sd
            audio = sd.rec(5 * 16000, samplerate=16000, channels=1, dtype="int16")
            sd.wait()
            audio_bytes = audio.tobytes()
            result = await stt.transcribe(audio_bytes)
            console.print(f"[green]Transcript:[/green] {result!r}")

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# test-tts command
# ---------------------------------------------------------------------------

@app.command("test-tts")
def test_tts(
    text_input: str = typer.Argument("Hello! I am Sorachio, your AI companion."),
    config: str | None = typer.Option(None, "--config", "-c"),
):
    """Test TTS synthesis and playback."""
    settings = _load_settings(config)
    _setup_logging(settings)

    async def _test():
        from tts.kokoro_client import KokoroTTSClient

        audio_queue: asyncio.Queue = asyncio.Queue()
        tts = KokoroTTSClient(
            audio_queue=audio_queue,
            voice=settings.tts.voice,
            speed=settings.tts.speed,
            sample_rate=settings.tts.sample_rate,
        )
        ok = await tts.initialize()
        if not ok:
            console.print("[red]TTS not available. Run: pip install kokoro[onnx][/red]")
            return

        console.print(f"[cyan]Synthesizing:[/cyan] {text_input!r}")
        await tts.speak(text_input)

        # Play back
        import sounddevice as sd
        while not audio_queue.empty():
            chunk = await audio_queue.get()
            if chunk is None:
                break
            sd.play(chunk, samplerate=settings.tts.sample_rate, blocking=True)

        console.print("[green][OK] TTS test complete[/green]")

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# test-cognitive command
# ---------------------------------------------------------------------------

@app.command("test-cognitive")
def test_cognitive(
    text_input: str = typer.Argument("Hey Sorachio, I've been really stressed about my exams."),
    config: str | None = typer.Option(None, "--config", "-c"),
    no_servers: bool = typer.Option(False, "--no-servers"),
):
    """Test Cognitive Gateway JSON analysis."""
    settings = _load_settings(config)
    _setup_logging(settings)

    async def _test():
        import json

        from cognition.cognitive_gateway import CognitiveGateway
        from llm.llama_client import LlamaClient
        from services.server_manager import ServerManager

        root = _project_root
        srv_mgr = None

        if not no_servers:
            srv_mgr = ServerManager(settings.llm, root)
            ok = await srv_mgr.start_all(wait_ready=True)
            if not ok:
                console.print("[red]Server start failed[/red]")
                return

        gw_cfg = settings.llm.cognitive_gateway
        client = LlamaClient(
            base_url=gw_cfg.server_url,
            temperature=gw_cfg.temperature,
            max_tokens=gw_cfg.max_tokens,
        )

        gateway = CognitiveGateway(client=client)
        console.print(f"[cyan]Analyzing:[/cyan] {text_input!r}")

        decision = await gateway.analyze(text_input)
        console.print(Panel(
            json.dumps(decision, indent=2),
            title="[bold]Cognitive Gateway Decision[/bold]",
            border_style="cyan",
        ))

        await client.close()
        if srv_mgr:
            srv_mgr.stop_all()

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# servers sub-commands
# ---------------------------------------------------------------------------

@servers_app.command("status")
def servers_status(config: str | None = typer.Option(None)):
    """Show status of llama-server instances."""
    settings = _load_settings(config)

    table = Table(title="LLM Servers", show_header=True)
    table.add_column("Name", style="cyan")
    table.add_column("Port")
    table.add_column("Model")
    table.add_column("Status")

    gw = settings.llm.cognitive_gateway
    pc = settings.llm.personality_core

    import httpx

    def check(url):
        try:
            r = httpx.get(f"{url}/health", timeout=2.0)
            return "[green]● Running[/green]" if r.status_code == 200 else "[red]● Error[/red]"
        except Exception:
            return "[red]● Offline[/red]"

    table.add_row("Cognitive Gateway (LLM #1)", str(gw.server_port), Path(gw.model_path).name, check(gw.server_url))
    table.add_row("Personality Core (LLM #2)", str(pc.server_port), Path(pc.model_path).name, check(pc.server_url))
    console.print(table)


@servers_app.command("start")
def servers_start(config: str | None = typer.Option(None)):
    """Start both llama-server instances."""
    settings = _load_settings(config)
    _setup_logging(settings)

    async def _start():
        from services.server_manager import ServerManager
        mgr = ServerManager(settings.llm, _project_root)
        ok = await mgr.start_all(wait_ready=True)
        if ok:
            console.print("[green][OK] Both servers started and ready[/green]")
        else:
            console.print("[red][FAIL] Server startup failed[/red]")

    asyncio.run(_start())


@servers_app.command("stop")
def servers_stop(config: str | None = typer.Option(None)):
    """Stop both llama-server instances."""
    settings = _load_settings(config)

    async def _stop():
        from services.server_manager import ServerManager
        mgr = ServerManager(settings.llm, _project_root)
        mgr.stop_all()
        console.print("[green][OK] Servers stopped[/green]")

    asyncio.run(_stop())


# ---------------------------------------------------------------------------
# memory sub-commands
# ---------------------------------------------------------------------------

@memory_app.command("list")
def memory_list(config: str | None = typer.Option(None)):
    """List all long-term memories."""
    settings = _load_settings(config)
    _setup_logging(settings)

    async def _list():
        from memory.long_term import LongTermMemory
        ltm = LongTermMemory(
            storage_path=str(_project_root / settings.memory.long_term.storage_path)
        )
        await ltm.initialize()
        stats = await ltm.get_stats()
        table = Table(title=f"Long-Term Memory ({stats['total_memories']} entries)")
        table.add_column("ID", style="dim")
        table.add_column("Topic")
        table.add_column("Importance")
        table.add_column("Content")
        for e in ltm._entries[-20:]:
            table.add_row(
                e.id, e.topic, f"{e.importance:.2f}", e.content[:60]
            )
        console.print(table)

    asyncio.run(_list())


@memory_app.command("clear")
def memory_clear(
    config: str | None = typer.Option(None),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Clear all long-term memories."""
    settings = _load_settings(config)
    if not yes:
        confirm = Prompt.ask("[red]Delete ALL memories?[/red] Type 'yes' to confirm")
        if confirm.lower() != "yes":
            console.print("Aborted.")
            return

    import json
    path = _project_root / settings.memory.long_term.storage_path
    if path.exists():
        path.write_text(json.dumps({"memories": []}))
        console.print("[green][OK] Memory cleared[/green]")
    else:
        console.print("[dim]No memory file found[/dim]")
