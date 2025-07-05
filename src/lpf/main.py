#!/usr/bin/env python3

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

# --- Optional Rich Import ---
try:
    from rich.console import Console
    from rich.table import Table

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# --- Configuration ---
# Store state and PID files in a dedicated directory for cleanliness
CONFIG_DIR = Path.home() / ".config" / "lpf"
STATE_FILE = CONFIG_DIR / "tunnels.json"
PID_DIR = CONFIG_DIR / "pids"

# --- Helper Functions ---


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
        Console().print(message)  # type: ignore
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


# --- CLI Commands ---


def add_tunnel(args):
    """Handler for the 'add' command."""
    ssh_host = args.ssh_host
    local_port = args.local_port
    # If remote_port isn't specified, it defaults to local_port
    remote_port = args.remote_port if args.remote_port else local_port

    if is_port_in_use(local_port):
        _print(f"[bold red]Error:[/] Local port {local_port} is already in use.")
        sys.exit(1)

    tunnel_id = f"{ssh_host}:{local_port}"
    tunnels = load_tunnels()

    if tunnel_id in tunnels and is_process_running(
        tunnels[tunnel_id].get("pid"), tunnels[tunnel_id]
    ):
        _print(
            f"[bold red]Error:[/] A tunnel for {tunnel_id} appears to be already active."
        )
        sys.exit(1)

    # Sanitize the tunnel_id to create a safe filename
    safe_filename = sanitize_filename(tunnel_id)
    pid_file = PID_DIR / f"{safe_filename}.pid"

    _print(f"Starting tunnel: localhost:{local_port} -> {ssh_host}:{remote_port}")

    # Construct the autossh command
    command = [
        "autossh",
        "-f",
        "-M",
        "0",
        "-N",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-L",
        f"{local_port}:localhost:{remote_port}",
        ssh_host,
    ]

    # Set environment variables for autossh
    env = os.environ.copy()
    env["AUTOSSH_PIDFILE"] = str(pid_file)
    env["AUTOSSH_GATETIME"] = "0"

    # Execute the command
    result = subprocess.run(command, env=env, capture_output=True, text=True)

    if result.returncode != 0:
        _print("[bold red]Error:[/] Failed to start autossh.")
        _print(f"Stderr: {result.stderr.strip()}")
        sys.exit(1)

    # Poll for the PID file to be created, with a timeout
    timeout = 5  # seconds
    start_time = time.time()
    pid = None
    while time.time() - start_time < timeout:
        if pid_file.exists():
            with open(pid_file, "r") as f:
                content = f.read().strip()
                if content:
                    try:
                        pid = int(content)
                        break
                    except ValueError:
                        # PID file is being written, wait a bit
                        pass
        time.sleep(0.1)

    if pid is None:
        _print(
            "[bold red]Error:[/] PID file was not created in time. Tunnel may have failed to start."
        )
        _print("Check `autossh` logs or try running the command manually.")
        sys.exit(1)

    # Update the state file
    tunnels[tunnel_id] = {
        "local_port": local_port,
        "remote_port": remote_port,
        "ssh_host": ssh_host,
        "pid": pid,
        "pid_file": str(pid_file),
    }
    save_tunnels(tunnels)

    _print(
        f"✅ [green]Tunnel '{tunnel_id}' started successfully with PID {pid}.[/green]"
    )


def list_tunnels(args):
    """Handler for the 'ls' command."""
    tunnels = load_tunnels()
    if not tunnels:
        _print("No tunnels are configured.")
        return

    if RICH_AVAILABLE:
        table = Table(  # type: ignore
            box=None,
            show_edge=False,
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("ID", style="cyan", no_wrap=True, min_width=25)
        table.add_column("STATUS", justify="center")
        table.add_column("FORWARDING", style="yellow", min_width=30)
        table.add_column("PID", justify="right", style="green")

        for tunnel_id, details in sorted(tunnels.items()):
            pid = details.get("pid")
            is_running = is_process_running(pid, details)
            status = "[green]ACTIVE[/green]" if is_running else "[red]INACTIVE[/red]"
            forwarding_str = f"localhost:{details['local_port']} → {details['ssh_host']}:{details['remote_port']}"
            pid_str = str(pid) if pid else "N/A"
            table.add_row(tunnel_id, status, forwarding_str, pid_str)

        Console().print(table)  # type: ignore
    else:
        # Fallback to plain text table
        print(f"{'ID':<30} {'STATUS':<10} {'FORWARDING':<30} {'PID'}")
        print(f"{'-' * 30} {'-' * 10} {'-' * 30} {'-' * 6}")

        for tunnel_id, details in sorted(tunnels.items()):
            pid = details.get("pid")
            status = "ACTIVE" if is_process_running(pid, details) else "INACTIVE"
            forwarding_str = f"localhost:{details['local_port']} -> localhost:{details['remote_port']}"
            pid_str = str(pid) if pid else "N/A"
            print(f"{tunnel_id:<30} {status:<10} {forwarding_str:<30} {pid_str}")


def stop_all_tunnels(args):
    """Handler for the 'stop-all' command."""
    tunnels = load_tunnels()
    if not tunnels:
        _print("No tunnels to stop.")
        return

    _print("Stopping all tunnels...")
    stopped_any = False
    for tunnel_id, details in list(tunnels.items()):
        if stop_tunnel_logic(tunnel_id, details, tunnels):
            stopped_any = True

    if stopped_any:
        _print("✅ [green]All tunnels stopped.[/green]")
    else:
        _print("All tunnels were already inactive.")


def cleanup_tunnel(tunnel_id, details, tunnels):
    """Remove tunnel from state and delete PID file."""
    pid_file = Path(details.get("pid_file", ""))
    if pid_file.exists():
        try:
            pid_file.unlink()
        except OSError as e:
            _print(f"[yellow]Warning:[/] Could not remove PID file {pid_file}: {e}")

    if tunnel_id in tunnels:
        del tunnels[tunnel_id]
        save_tunnels(tunnels)


def stop_tunnel_logic(tunnel_id, details, tunnels):
    """Logic to stop a single tunnel process and clean up."""
    pid = details.get("pid")
    stopped = False
    if pid and is_process_running(pid, details):
        _print(f"Stopping tunnel '{tunnel_id}' (PID: {pid})...")
        try:
            os.kill(pid, signal.SIGTERM)
            _print("✅ [green]Tunnel stopped.[/green]")
            stopped = True
        except OSError as e:
            _print(
                f"[yellow]Warning:[/] Could not kill process {pid}. It might already be stopped. Error: {e}"
            )
    else:
        _print(f"Tunnel '{tunnel_id}' is already inactive.")

    cleanup_tunnel(tunnel_id, details, tunnels)
    return stopped


def stop_tunnel(args):
    """Handler for the 'stop' command."""
    local_port_to_stop = args.local_port
    tunnels = load_tunnels()

    tunnel_to_stop = None
    for tunnel_id, details in tunnels.items():
        if details.get("local_port") == local_port_to_stop:
            tunnel_to_stop = (tunnel_id, details)
            break

    if not tunnel_to_stop:
        _print(
            f"[bold red]Error:[/] No active tunnel found for local port '{local_port_to_stop}'."
        )
        _print("Use 'lpf ls' to see available tunnels.")
        sys.exit(1)

    tunnel_id_to_stop, details_to_stop = tunnel_to_stop
    stop_tunnel_logic(tunnel_id_to_stop, details_to_stop, tunnels)


def main():
    """Main function to parse arguments and dispatch to handlers."""
    ensure_config_dirs()

    parser = argparse.ArgumentParser(
        description="A CLI to manage SSH local port forwarding tunnels.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Available commands"
    )

    # --- Parser for 'add' command ---
    parser_add = subparsers.add_parser("add", help="Add and start a new SSH tunnel.")
    parser_add.add_argument("local_port", type=int, help="The local port to listen on.")
    parser_add.add_argument(
        "ssh_host", help="The SSH host to connect to (e.g., user@hpc.server.edu)."
    )
    parser_add.add_argument(
        "remote_port",
        type=int,
        nargs="?",
        default=None,
        help="The remote port on the SSH host. Defaults to local_port if not specified.",
    )
    parser_add.set_defaults(func=add_tunnel)

    # --- Parser for 'ls' (list) command ---
    parser_ls = subparsers.add_parser(
        "ls", help="List all configured tunnels and their status.", aliases=["list"]
    )
    parser_ls.set_defaults(func=list_tunnels)

    # --- Parser for 'stop' command ---
    parser_stop = subparsers.add_parser(
        "stop",
        help="Stop and remove a configured tunnel by local port.",
        aliases=["rm", "remove"],
    )
    parser_stop.add_argument(
        "local_port", type=int, help="The local port of the tunnel to stop."
    )
    parser_stop.set_defaults(func=stop_tunnel)

    # --- Parser for 'stop-all' command ---
    parser_stop_all = subparsers.add_parser(
        "stop-all",
        help="Stop all active tunnels.",
        aliases=["kill-all", "remove-all"],
    )
    parser_stop_all.set_defaults(func=stop_all_tunnels)

    # --- Parser for 'help' command ---
    def print_help(args):
        """Prints the main help message."""
        parser.print_help()

    parser_help = subparsers.add_parser("help", help="Show this help message and exit.")
    parser_help.set_defaults(func=print_help)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        # If no command is given, print help
        parser.print_help()


if __name__ == "__main__":
    main()
