#!/usr/bin/env python3
import typer
from . import commands
from .utils import ensure_config_dirs

app = typer.Typer(
    name="lpf",
    help="A CLI tool to manage local port forwarding tunnels with autossh.",
    add_completion=False,
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
):
    """Add and start a new tunnel."""
    commands.add_tunnel(ssh_host, local_port, remote_port)


@app.command("ls", help="List all configured tunnels and their status")
def list_tunnels_command():
    """List all configured tunnels and their status."""
    commands.list_tunnels()


def main():
    """Main entry point for the lpf-cli command."""
    ensure_config_dirs()
    app()


if __name__ == "__main__":
    main()
