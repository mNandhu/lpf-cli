"""
Utility functions for lpf-cli.
"""

import json
import os
import re
import socket
import sys
from .config import CONFIG_DIR, PID_DIR, STATE_FILE
from rich.console import Console

console = Console()


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
                console.print(
                    f"[bold red]Error:[/] Unexpected error checking port {port}: {e}"
                )
                return True


def forward_spec(details: dict) -> str:
    """Build the autossh -L argument for a tunnel.

    remote_host defaults to "localhost" so tunnels saved before -H existed keep
    producing the string their running process was started with -- otherwise
    is_process_running would read every one of them as a stale PID.
    """
    remote_host = details.get("remote_host") or "localhost"
    return f"{details['local_port']}:{remote_host}:{details['remote_port']}"


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
            expected_l_flag = forward_spec(tunnel_details)
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
