"""
Configuration for lpf-cli.
"""

from pathlib import Path

# Store state and PID files in a dedicated directory for cleanliness
CONFIG_DIR = Path.home() / ".config" / "lpf"
STATE_FILE = CONFIG_DIR / "tunnels.json"
PID_DIR = CONFIG_DIR / "pids"
