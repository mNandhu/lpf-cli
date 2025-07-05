"""
CLI command functions for lpf-cli.
"""

import os
import subprocess
import sys
import time
from rich.table import Table

from .utils import (
    console,
    is_port_in_use,
    is_process_running,
    load_tunnels,
    sanitize_filename,
    save_tunnels,
)
from .config import PID_DIR


def add_tunnel(ssh_host: str, local_port: int, remote_port: int | None):
    """Handler for the 'add' command."""
    # If remote_port isn't specified, it defaults to local_port
    remote_port = remote_port if remote_port else local_port

    if is_port_in_use(local_port):
        console.print(f"[bold red]Error:[/] Local port {local_port} is already in use.")
        sys.exit(1)

    tunnel_id = f"{ssh_host}:{local_port}"
    tunnels = load_tunnels()

    if tunnel_id in tunnels and is_process_running(
        tunnels[tunnel_id].get("pid"), tunnels[tunnel_id]
    ):
        console.print(
            f"[bold red]Error:[/] A tunnel for {tunnel_id} appears to be already active."
        )
        sys.exit(1)

    # Sanitize the tunnel_id to create a safe filename
    safe_filename = sanitize_filename(tunnel_id)
    pid_file = PID_DIR / f"{safe_filename}.pid"

    console.print(
        f"Starting tunnel: localhost:{local_port} -> {ssh_host}:{remote_port}"
    )

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
        console.print("[bold red]Error:[/] Failed to start autossh.")
        console.print(f"Stderr: {result.stderr.strip()}")
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
        console.print(
            "[bold red]Error:[/] PID file was not created in time. Tunnel may have failed to start."
        )
        console.print("Check `autossh` logs or try running the command manually.")
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

    console.print(
        f"✅ [green]Tunnel '{tunnel_id}' started successfully with PID {pid}.[/green]"
    )


def list_tunnels():
    """Handler for the 'ls' command."""
    tunnels = load_tunnels()
    if not tunnels:
        console.print("No tunnels are configured.")
        return

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
        status = "[green]● ACTIVE[/green]" if is_running else "[red]● INACTIVE[/red]"
        forwarding_str = (
            f"localhost:{details['local_port']} -> localhost:{details['remote_port']}"
        )
        table.add_row(tunnel_id, status, forwarding_str)

    console.print(table)


def remove_tunnel(tunnel_id: str):
    """Handler for the 'rm' command."""
    tunnels = load_tunnels()

    if tunnel_id not in tunnels:
        console.print(f"[bold red]Error:[/] Tunnel '{tunnel_id}' not found.")
        sys.exit(1)

    details = tunnels[tunnel_id]
    pid = details.get("pid")

    if pid and is_process_running(pid, details):
        console.print(f"Stopping tunnel '{tunnel_id}' (PID: {pid})...")
        try:
            os.kill(pid, 15)  # Send SIGTERM
        except OSError as e:
            console.print(f"[bold red]Error:[/] Failed to stop process {pid}: {e}")

    # Clean up PID file
    pid_file_path = details.get("pid_file")
    if pid_file_path:
        try:
            os.remove(pid_file_path)
        except FileNotFoundError:
            pass  # It's already gone, which is fine
        except OSError as e:
            console.print(
                f"[bold red]Warning:[/] Could not remove PID file {pid_file_path}: {e}"
            )

    # Remove from state and save
    del tunnels[tunnel_id]
    save_tunnels(tunnels)

    console.print(f"✅ [green]Tunnel '{tunnel_id}' removed successfully.[/green]")


def remove_all_tunnels():
    """Handler for the 'rm --all' command."""
    tunnels = load_tunnels()
    if not tunnels:
        console.print("No tunnels to remove.")
        return

    console.print(f"Removing all {len(tunnels)} tunnels...")
    for tunnel_id in list(tunnels.keys()):
        remove_tunnel(tunnel_id)

    console.print("✅ [green]All tunnels removed successfully.[/green]")
