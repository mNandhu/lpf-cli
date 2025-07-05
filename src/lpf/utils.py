"""
Utility functions for lpf-cli.
"""

import json
import os
import re
import socket
import sys
from .config import CONFIG_DIR, PID_DIR, STATE_FILE

# --- Optional Rich Import ---
try:
    from rich.console import Console

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def ensure_config_dirs():
    """Ensure the configuration and PID directories exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PID_DIR.mkdir(exist_ok=True)


def load_tunnels():
    """Load the list of managed tunnels from the state file."""
    if not STATE_FILE.exists():
        return {}
    with open(STATE_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            # Handle case where file is empty or corrupt
            return {}


def save_tunnels(tunnels):
    """Save the list of tunnels to the state file."""
    with open(STATE_FILE, "w") as f:
        json.dump(tunnels, f, indent=2)


def _print(message):
    """Helper to print with rich if available, otherwise use standard print."""
    if RICH_AVAILABLE:
        console = Console() # type: ignore
        console.print(message)
    else:
        # Strip rich markup for plain print
        plain_message = re.sub(r"\[(.*?)\]", "", message)
        print(plain_message)


def sanitize_filename(name):
    """Replace special characters in a string to make it a valid filename."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name)


def is_port_in_use(port):
    """Check if a local port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            # Try to bind to the port. If it fails, the port is in use.
            s.bind(("127.0.0.1", port))
            return False
        except OSError as e:
            if e.errno == 98:  # Address already in use
                return True
            else:
                _print(
                    f"[bold red]Error:[/] Unexpected error checking port {port}: {e}"
                )
                return True


def is_process_running(pid, tunnel_details=None):
    """
    Check if a process with the given PID is running.
    If tunnel_details are provided, also verify it's the correct autossh process.
    """
    if pid is None:
        return False
    try:
        # os.kill(pid, 0) doesn't send a signal, but checks for process existence.
        os.kill(pid, 0)
    except OSError:
        return False

    if sys.platform == "linux" and tunnel_details:
        try:
            with open(f"/proc/{pid}/cmdline", "r") as f:
                # cmdline is null-byte separated
                cmdline = f.read().strip().split("\0")
            # Check if it's an autossh command for the correct port and host
            expected_l_flag = f"{tunnel_details['local_port']}:localhost:{tunnel_details['remote_port']}"
            if (
                "autossh" in cmdline[0]
                and expected_l_flag in cmdline
                and tunnel_details["ssh_host"] in cmdline
            ):
                return True
            else:
                # PID exists but doesn't match our command, so it's a stale PID
                return False
        except FileNotFoundError:
            # Process disappeared between os.kill and reading cmdline
            return False

    # For non-Linux or when no details are provided, fall back to original check
    return True
