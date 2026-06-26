"""
Sorachio-STS — Main Entry Point
================================
Run this to start the full AI companion pipeline.

Usage:
    python main.py                    # Full voice mode
    python main.py --text             # Text input mode
    python main.py --text -m "Hello"  # Single message test

Delegates all logic to cli/main.py (Typer app).
"""

import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from cli.main import app

if __name__ == "__main__":
    app()
