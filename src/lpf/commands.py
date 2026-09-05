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
    forward_spec,
    is_port_in_use,
    is_process_running,
    load_tunnels,
    sanitize_filename,
    save_tunnels,
)
from .config import PID_DIR


def _start_tunnel_process(tunnel_id: str, details: dict) -> int | None:
    """Starts the autossh process for a given tunnel and returns the PID."""
    safe_filename = sanitize_filename(tunnel_id)
    pid_file = PID_DIR / f"{safe_filename}.pid"

    remote_host = details.get("remote_host") or "localhost"
    console.print(
        f"Starting tunnel: localhost:{details['local_port']} -> "
        f"{details['ssh_host']}:{remote_host}:{details['remote_port']}"
    )

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
        forward_spec(details),
        details["ssh_host"],
    ]

    env = os.environ.copy()
    env["AUTOSSH_PIDFILE"] = str(pid_file)
    env["AUTOSSH_GATETIME"] = "0"

    result = subprocess.run(command, env=env, capture_output=True, text=True)

    if result.returncode != 0:
        console.print("[bold red]Error:[/] Failed to start autossh.")
        console.print(f"Stderr: {result.stderr.strip()}")
        return None

    timeout = 5
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
                        pass
        time.sleep(0.1)

    if pid is None:
        console.print(
            "[bold red]Error:[/] PID file was not created in time. Tunnel may have failed to start."
        )
        return None

    return pid


def add_tunnel(
    ssh_host: str,
    local_port: int,
    remote_port: int | None,
    force: bool = False,
    remote_host: str = "localhost",
):
    """Handler for the 'add' command."""
    # If remote_port isn't specified, it defaults to local_port
    remote_port = remote_port if remote_port else local_port
    tunnels = load_tunnels()

    # --- Conflict detection ---
    # Check lpf's own registered tunnels first: a stopped/inactive tunnel
    # still "owns" its local_port even though nothing is bound to it at the
    # OS level, so is_port_in_use() alone would miss the conflict.
    existing_tunnel_id = next(
        (tid for tid, d in tunnels.items() if d.get("local_port") == local_port),
        None,
    )

    if existing_tunnel_id:
        if not force:
            console.print(
                f"[bold red]Error:[/] Local port {local_port} is already assigned to "
                f"tunnel '{existing_tunnel_id}'. Use --force to replace it, or run "
                f"'lpf rm {existing_tunnel_id}' first."
            )
            sys.exit(1)
        console.print(
            f"[yellow]Port {local_port} is already assigned to tunnel "
            f"'{existing_tunnel_id}'. Forcing removal.[/yellow]"
        )
        remove_tunnel(existing_tunnel_id)
        # Reload tunnels state after removal
        tunnels = load_tunnels()
    elif is_port_in_use(local_port):
        # No lpf tunnel claims this port, so it's held by an external
        # process -- force can't help here, there's nothing of ours to remove.
        if force:
            console.print(
                f"[bold red]Error:[/] Local port {local_port} is in use by an external process. Cannot override."
            )
        else:
            console.print(
                f"[bold red]Error:[/] Local port {local_port} is already in use. Use --force to override."
            )
        sys.exit(1)

    tunnel_id = f"{ssh_host}:{local_port}"

    if tunnel_id in tunnels:
        console.print(f"[bold red]Error:[/] A tunnel for {tunnel_id} already exists.")
        sys.exit(1)

    # Create the tunnel configuration
    tunnels[tunnel_id] = {
        "local_port": local_port,
        "remote_port": remote_port,
        "ssh_host": ssh_host,
        # Resolved on the SSH server, not here: "localhost" means the server
        # itself, anything else is a host the server can reach (a container IP,
        # another machine on its network).
        "remote_host": remote_host,
    }

    # Start the tunnel process
    pid = _start_tunnel_process(tunnel_id, tunnels[tunnel_id])

    if pid:
        tunnels[tunnel_id]["pid"] = pid
        tunnels[tunnel_id]["pid_file"] = str(
            PID_DIR / f"{sanitize_filename(tunnel_id)}.pid"
        )
        save_tunnels(tunnels)
        console.print(
            f"[green]Tunnel '{tunnel_id}' started successfully with PID {pid}.[/green]"
        )
    else:
        # Clean up the failed tunnel entry
        del tunnels[tunnel_id]
        save_tunnels(tunnels)
        sys.exit(1)


def list_tunnels():
    """Handler for the 'ls' command."""
    # Sync first to clean up any stale tunnels
    sync_tunnels(silent=True)

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
        if details.get("stopped"):
            status = "[yellow]STOPPED[/yellow]"
        elif is_process_running(details.get("pid"), details):
            status = "[green]ACTIVE[/green]"
        else:
            status = "[red]INACTIVE[/red]"
        remote_host = details.get("remote_host") or "localhost"
        forwarding_str = (
            f"localhost:{details['local_port']} -> "
            f"{remote_host}:{details['remote_port']}"
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

    console.print(f"[green]Tunnel '{tunnel_id}' removed successfully.[/green]")


def remove_all_tunnels():
    """Handler for the 'rm --all' command."""
    tunnels = load_tunnels()
    if not tunnels:
        console.print("No tunnels to remove.")
        return

    console.print(f"Removing all {len(tunnels)} tunnels...")
    for tunnel_id in list(tunnels.keys()):
        remove_tunnel(tunnel_id)

    console.print("[green]All tunnels removed successfully.[/green]")


def stop_tunnel(tunnel_id: str):
    """Handler for the 'stop' command - temporarily stops a tunnel without removing it."""
    tunnels = load_tunnels()

    if tunnel_id not in tunnels:
        console.print(f"[bold red]Error:[/] Tunnel '{tunnel_id}' not found.")
        sys.exit(1)

    details = tunnels[tunnel_id]

    if details.get("stopped"):
        console.print(f"[yellow]Tunnel '{tunnel_id}' is already stopped.[/yellow]")
        return

    pid = details.get("pid")
    if pid and is_process_running(pid, details):
        console.print(f"Stopping tunnel '{tunnel_id}' (PID: {pid})...")
        try:
            os.kill(pid, 15)  # SIGTERM
        except OSError as e:
            console.print(f"[bold red]Error:[/] Failed to stop process {pid}: {e}")

    pid_file_path = details.get("pid_file")
    if pid_file_path:
        try:
            os.remove(pid_file_path)
        except (FileNotFoundError, OSError):
            pass

    tunnels[tunnel_id].pop("pid", None)
    tunnels[tunnel_id].pop("pid_file", None)
    tunnels[tunnel_id]["stopped"] = True
    save_tunnels(tunnels)
    console.print(
        f"[green]Tunnel '{tunnel_id}' stopped. Use 'lpf start {tunnel_id}' to resume.[/green]"
    )


def stop_all_tunnels():
    """Handler for 'stop --all'."""
    tunnels = load_tunnels()
    if not tunnels:
        console.print("No tunnels to stop.")
        return

    console.print(f"Stopping all {len(tunnels)} tunnels...")
    for tunnel_id in list(tunnels.keys()):
        stop_tunnel(tunnel_id)


def start_tunnel(tunnel_id: str):
    """Handler for the 'start' command - starts a stopped or inactive tunnel."""
    tunnels = load_tunnels()

    if tunnel_id not in tunnels:
        console.print(f"[bold red]Error:[/] Tunnel '{tunnel_id}' not found.")
        sys.exit(1)

    details = tunnels[tunnel_id]

    if not details.get("stopped") and is_process_running(details.get("pid"), details):
        console.print(f"[yellow]Tunnel '{tunnel_id}' is already running.[/yellow]")
        return

    pid = _start_tunnel_process(tunnel_id, details)
    if pid:
        tunnels[tunnel_id]["pid"] = pid
        tunnels[tunnel_id]["pid_file"] = str(
            PID_DIR / f"{sanitize_filename(tunnel_id)}.pid"
        )
        tunnels[tunnel_id].pop("stopped", None)
        save_tunnels(tunnels)
        console.print(
            f"[green]Tunnel '{tunnel_id}' started with PID {pid}.[/green]"
        )
    else:
        sys.exit(1)


def start_all_tunnels():
    """Handler for 'start --all'."""
    tunnels = load_tunnels()
    if not tunnels:
        console.print("No tunnels configured.")
        return

    console.print(f"Starting all {len(tunnels)} tunnels...")
    for tunnel_id in list(tunnels.keys()):
        start_tunnel(tunnel_id)


def restart_tunnels(force: bool = False):
    """Finds all inactive tunnels and restarts them. With --force, restarts all (including stopped)."""
    sync_tunnels(silent=True)
    tunnels = load_tunnels()
    restarted_count = 0

    if force:
        console.print("Forcing restart of all tunnels...")
    else:
        console.print("Checking for inactive tunnels to restart...")

    for tunnel_id, details in list(tunnels.items()):
        # Skip intentionally stopped tunnels unless forcing
        if details.get("stopped") and not force:
            continue

        is_running = is_process_running(details.get("pid"), details)

        if force and is_running:
            console.print(f"Stopping active tunnel: [cyan]{tunnel_id}[/cyan]")
            # Stop the process without removing the config
            pid = details.get("pid")
            try:
                os.kill(pid, 15)  # Send SIGTERM
                time.sleep(0.5)  # Give it a moment to die
            except OSError:
                pass  # Already dead, probably

        # Start if it was forced or if it was inactive
        if force or not is_running:
            console.print(f"Starting tunnel: [cyan]{tunnel_id}[/cyan]")
            pid = _start_tunnel_process(tunnel_id, details)
            if pid:
                tunnels[tunnel_id]["pid"] = pid
                tunnels[tunnel_id]["pid_file"] = str(
                    PID_DIR / f"{sanitize_filename(tunnel_id)}.pid"
                )
                tunnels[tunnel_id].pop("stopped", None)
                restarted_count += 1
            else:
                console.print(
                    f"[bold red]Failed to restart tunnel '{tunnel_id}'.[/bold red]"
                )

    if restarted_count > 0:
        save_tunnels(tunnels)
        console.print(
            f"[green]Finished. Restarted {restarted_count} tunnel(s).[/green]"
        )
    else:
        console.print("[green]No tunnels needed restarting.[/green]")


def sync_tunnels(silent: bool = False):
    """Syncs the state of tunnels, cleaning up stale entries."""
    tunnels = load_tunnels()
    if not tunnels:
        if not silent:
            console.print("No tunnels to sync.")
        return

    stale_count = 0
    with console.status("[bold green]Syncing tunnel states...[/]"):
        tunnels_to_check = list(tunnels.items())
        for tunnel_id, details in tunnels_to_check:
            if details.get("stopped"):
                continue
            pid = details.get("pid")
            if pid and not is_process_running(pid, details):
                if not silent:
                    console.print(
                        f"[yellow]Stale PID found for tunnel '{tunnel_id}'. Cleaning up.[/yellow]"
                    )
                # Remove stale PID info from the original dict
                del tunnels[tunnel_id]["pid"]
                if "pid_file" in tunnels[tunnel_id]:
                    del tunnels[tunnel_id]["pid_file"]
                stale_count += 1

    if stale_count > 0:
        save_tunnels(tunnels)
        if not silent:
            console.print(
                f"[green]Sync complete. Cleaned up {stale_count} stale tunnel(s).[/green]"
            )
    else:
        if not silent:
            console.print("[green]All tunnels are in sync.[/green]")
