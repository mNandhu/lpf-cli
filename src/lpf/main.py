#!/usr/bin/env python3
import typer
from . import commands
from .utils import ensure_config_dirs, console, load_tunnels


def _complete_tunnel_id(incomplete: str):
    return [tid for tid in load_tunnels() if tid.startswith(incomplete)]

app = typer.Typer(
    name="lpf",
    help="A CLI tool to manage local port forwarding tunnels with autossh.",
    add_completion=True,
)


@app.command("add", help="Add and start a new tunnel")
def add_tunnel_command(
    ssh_host: str = typer.Argument(..., help="The SSH host (e.g., user@hostname)"),
    local_port: int = typer.Argument(..., help="The local port to forward from"),
    remote_port: int = typer.Option(
        None,
        "--remote-port",
        "-r",
        help="The remote port to forward to (defaults to local_port)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force creation, removing any existing tunnel on the same local port.",
    ),
    remote_host: str = typer.Option(
        "localhost",
        "--remote-host",
        "-H",
        help="Host the SSH server forwards to (defaults to the server itself). "
        "Use this to reach something the server can see but does not listen on, "
        "e.g. a container IP.",
    ),
):
    """Add and start a new tunnel."""
    commands.add_tunnel(ssh_host, local_port, remote_port, force, remote_host)


@app.command("ls", help="List all configured tunnels and their status")
def list_tunnels_command():
    """List all configured tunnels and their status."""
    commands.list_tunnels()


@app.command("rm", help="Stop and remove a tunnel")
def remove_tunnel_command(
    tunnel_id: str = typer.Argument(
        None,
        help="The ID of the tunnel to remove (e.g., user@hostname:port)",
        autocompletion=_complete_tunnel_id,
    ),
    all: bool = typer.Option(
        False, "--all", "-a", help="Remove all configured tunnels."
    ),
):
    """Stop and remove a tunnel."""
    if all:
        commands.remove_all_tunnels()
    elif tunnel_id:
        commands.remove_tunnel(tunnel_id)
    else:
        console.print(
            "[bold red]Error:[/] Please provide a tunnel ID or use the --all flag."
        )
        raise typer.Exit(code=1)


@app.command("stop", help="Temporarily stop a tunnel without removing it")
def stop_tunnel_command(
    tunnel_id: str = typer.Argument(
        None,
        help="The ID of the tunnel to stop (e.g., user@hostname:port)",
        autocompletion=_complete_tunnel_id,
    ),
    all: bool = typer.Option(
        False, "--all", "-a", help="Stop all configured tunnels."
    ),
):
    """Temporarily stop a tunnel without removing it."""
    if all:
        commands.stop_all_tunnels()
    elif tunnel_id:
        commands.stop_tunnel(tunnel_id)
    else:
        console.print(
            "[bold red]Error:[/] Please provide a tunnel ID or use the --all flag."
        )
        raise typer.Exit(code=1)


@app.command("start", help="Start a stopped or inactive tunnel")
def start_tunnel_command(
    tunnel_id: str = typer.Argument(
        None,
        help="The ID of the tunnel to start (e.g., user@hostname:port)",
        autocompletion=_complete_tunnel_id,
    ),
    all: bool = typer.Option(
        False, "--all", "-a", help="Start all configured tunnels."
    ),
):
    """Start a stopped or inactive tunnel."""
    if all:
        commands.start_all_tunnels()
    elif tunnel_id:
        commands.start_tunnel(tunnel_id)
    else:
        console.print(
            "[bold red]Error:[/] Please provide a tunnel ID or use the --all flag."
        )
        raise typer.Exit(code=1)


@app.command("restart", help="Restart all tunnels")
def restart_tunnels_command(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force restart of all tunnels, even active ones.",
    ),
):
    """Restart all tunnels."""
    commands.restart_tunnels(force)


@app.command("sync", help="Sync the state of tunnels with the system")
def sync_tunnels_command():
    """Sync the state of tunnels with the system."""
    commands.sync_tunnels()


def main():
    """Main entry point for the lpf-cli command."""
    ensure_config_dirs()
    app()


if __name__ == "__main__":
    main()
