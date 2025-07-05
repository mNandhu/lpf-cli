"""
CLI command functions for lpf-cli.
"""

import os
import subprocess
import sys
import time

from .utils import (
    _print,
    is_port_in_use,
    is_process_running,
    load_tunnels,
    sanitize_filename,
    save_tunnels,
)
from .config import PID_DIR


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
                        # PID file might be being written, wait a bit
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

    # Rich is an optional dependency, so we check for it here
    try:
        from rich.table import Table
        from rich.console import Console

        table = Table(
            box=None,
            show_edge=False,
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("ID", style="cyan", no_wrap=True, min_width=25)
        table.add_column("STATUS", justify="center")
        table.add_column("FORWARDING", style="yellow", min_width=30)

        for tunnel_id, details in sorted(tunnels.items()):
            pid = details.get("pid")
            is_running = is_process_running(pid, details)
            status = (
                "[green]● ACTIVE[/green]" if is_running else "[red]● INACTIVE[/red]"
            )
            forwarding_str = f"localhost:{details['local_port']} -> localhost:{details['remote_port']}"
            table.add_row(tunnel_id, status, forwarding_str)

        Console().print(table)

    except ImportError:
        # Fallback to plain text output
        for tunnel_id, details in sorted(tunnels.items()):
            pid = details.get("pid")
            is_running = is_process_running(pid, details)
            status = "ACTIVE" if is_running else "INACTIVE"
            forwarding_str = f"localhost:{details['local_port']} -> localhost:{details['remote_port']}"
            _print(f"{tunnel_id}: {status} ({forwarding_str})")
