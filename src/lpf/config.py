"""
Configuration for lpf-cli.
"""

import os
from pathlib import Path

# Store state, PID files, and logs in a dedicated directory for cleanliness.
# LPF_CONFIG_DIR overrides it, e.g. to keep tests away from real tunnels.
CONFIG_DIR = Path(
    os.environ.get("LPF_CONFIG_DIR") or Path.home() / ".config" / "lpf"
)
STATE_FILE = CONFIG_DIR / "tunnels.json"
PID_DIR = CONFIG_DIR / "pids"
LOG_DIR = CONFIG_DIR / "logs"
