#!/usr/bin/env python3
from importlib.metadata import version

import typer
from . import autostart, commands
from .update_check import maybe_notify
from .utils import ensure_config_dirs, console, load_tunnels, ssh_config_hosts


def _complete_tunnel_id(incomplete: str):
    """Complete a tunnel ID or name, e.g. 'myserver:8000' or 'host1_grafana'."""
    matches = []
    for tunnel_id, details in load_tunnels().items():
        if tunnel_id.startswith(incomplete):
            matches.append(tunnel_id)
        name = details.get("name")
        if name and name.startswith(incomplete) and name not in matches:
            matches.append(name)
    return matches


def _complete_port(ctx: typer.Context, incomplete: str):
    """Complete PORT with local ports of tunnels matching the SSH host typed so far."""
    ssh_host = ctx.params.get("tunnel_id")
    ports = {
        str(details["local_port"])
        for details in load_tunnels().values()
        if not ssh_host or details.get("ssh_host") == ssh_host
    }
    return [p for p in ports if p.startswith(incomplete)]


def _complete_ssh_host(incomplete: str):
    """Complete SSH_HOST with the Host aliases in ~/.ssh/config."""
    return [host for host in ssh_config_hosts() if host.startswith(incomplete)]


def _resolve_tunnel_id(identifier: str | None, port: int | None) -> str | None:
    """Combine `SSH_HOST PORT` into `SSH_HOST:PORT`, mirroring `lpf add`.

    `identifier` may already be a full `host:port` tunnel ID; PORT is only
    used when passed separately.
    """
    if port is None:
        return identifier
    if identifier and ":" in identifier:
        console.print(
            "[bold red]Error:[/] Got a full tunnel ID and a separate PORT. "
            "Pass either 'SSH_HOST:PORT' or 'SSH_HOST PORT', not both."
        )
        raise typer.Exit(code=1)
    return f"{identifier}:{port}"


app = typer.Typer(
    name="lpf",
    help="A CLI tool to manage local port forwarding tunnels with autossh.",
    add_completion=True,
)


def _version_callback(value: bool):
    if value:
        console.print(f"lpf {version('lpf-cli')}", highlight=False)
        raise typer.Exit()


@app.callback()
def app_callback(
    show_version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the installed version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
):
    pass


NO_WAIT_HELP = "Don't wait for tunnels to connect. autossh keeps retrying in the background."


@app.command("add", help="Add and start new tunnels, one per local port")
def add_tunnel_command(
    ssh_host: str = typer.Argument(
        ...,
        help="The SSH host (e.g., user@hostname or an alias from ~/.ssh/config)",
        autocompletion=_complete_ssh_host,
    ),
    local_ports: list[int] = typer.Argument(
        ...,
        help="One or more local ports to forward from (e.g., lpf add myserver 8000 8001)",
        min=1,
        max=65535,
    ),
    remote_port: int = typer.Option(
        None,
        "--remote-port",
        "-r",
        help="The remote port to forward to (defaults to the local port). "
        "Only works with a single local port.",
        min=1,
        max=65535,
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
    name: str = typer.Option(
        None,
        "--name",
        "-n",
        help="A memorable name for the tunnel (letters, digits, '.', '_', '-'), so you "
        "can 'lpf start <name>' instead of the full tunnel ID. Only works with a "
        "single local port.",
    ),
):
    """Add and start new tunnels, one per local port."""
    commands.add_tunnel(ssh_host, local_ports, remote_port, force, remote_host, name)


@app.command("ls", help="List all configured tunnels and their status")
def list_tunnels_command():
    """List all configured tunnels and their status."""
    commands.list_tunnels()


@app.command("rm", help="Stop and remove a tunnel")
def remove_tunnel_command(
    tunnel_id: str | None = typer.Argument(
        None,
        help="The SSH host, full tunnel ID, or --name given at 'lpf add' "
        "(e.g., user@hostname, user@hostname:port, or host1_grafana)",
        autocompletion=_complete_tunnel_id,
    ),
    port: int | None = typer.Argument(
        None,
        help="The local port, if SSH_HOST was given without ':port' (e.g., lpf rm user@hostname 8080)",
        autocompletion=_complete_port,
    ),
    all: bool = typer.Option(
        False, "--all", "-a", help="Remove all configured tunnels."
    ),
):
    """Stop and remove a tunnel."""
    tunnel_id = _resolve_tunnel_id(tunnel_id, port)
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
    tunnel_id: str | None = typer.Argument(
        None,
        help="The SSH host, full tunnel ID, or --name given at 'lpf add' "
        "(e.g., user@hostname, user@hostname:port, or host1_grafana)",
        autocompletion=_complete_tunnel_id,
    ),
    port: int | None = typer.Argument(
        None,
        help="The local port, if SSH_HOST was given without ':port' (e.g., lpf stop user@hostname 8080)",
        autocompletion=_complete_port,
    ),
    all: bool = typer.Option(
        False, "--all", "-a", help="Stop all configured tunnels."
    ),
):
    """Temporarily stop a tunnel without removing it."""
    tunnel_id = _resolve_tunnel_id(tunnel_id, port)
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
    tunnel_id: str | None = typer.Argument(
        None,
        help="The SSH host, full tunnel ID, or --name given at 'lpf add' "
        "(e.g., user@hostname, user@hostname:port, or host1_grafana)",
        autocompletion=_complete_tunnel_id,
    ),
    port: int | None = typer.Argument(
        None,
        help="The local port, if SSH_HOST was given without ':port' (e.g., lpf start user@hostname 8080)",
        autocompletion=_complete_port,
    ),
    all: bool = typer.Option(
        False, "--all", "-a", help="Start all configured tunnels."
    ),
    no_wait: bool = typer.Option(False, "--no-wait", help=NO_WAIT_HELP),
):
    """Start a stopped or inactive tunnel."""
    tunnel_id = _resolve_tunnel_id(tunnel_id, port)
    if all:
        commands.start_all_tunnels(wait=not no_wait)
    elif tunnel_id:
        commands.start_tunnel(tunnel_id, wait=not no_wait)
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
    no_wait: bool = typer.Option(False, "--no-wait", help=NO_WAIT_HELP),
):
    """Restart all tunnels."""
    commands.restart_tunnels(force, wait=not no_wait)


@app.command("sync", help="Sync the state of tunnels with the system")
def sync_tunnels_command():
    """Sync the state of tunnels with the system."""
    commands.sync_tunnels()


@app.command("logs", help="Show a tunnel's autossh and ssh log")
def logs_command(
    tunnel_id: str = typer.Argument(
        ...,
        help="The SSH host, full tunnel ID, or --name given at 'lpf add' "
        "(e.g., user@hostname, user@hostname:port, or host1_grafana)",
        autocompletion=_complete_tunnel_id,
    ),
    port: int | None = typer.Argument(
        None,
        help="The local port, if SSH_HOST was given without ':port' (e.g., lpf logs user@hostname 8080)",
        autocompletion=_complete_port,
    ),
    lines: int = typer.Option(
        50, "--lines", "-n", min=0, help="Number of lines to show from the end."
    ),
    follow: bool = typer.Option(
        False, "--follow", "-f", help="Keep printing new lines as they're written."
    ),
):
    """Show a tunnel's autossh and ssh log."""
    resolved = _resolve_tunnel_id(tunnel_id, port)
    assert resolved is not None
    commands.show_logs(resolved, lines, follow)


autostart_app = typer.Typer(
    help="Start tunnels automatically at login or boot (systemd).",
    no_args_is_help=True,
)
app.add_typer(autostart_app, name="autostart")


@autostart_app.command("enable", help="Install and enable a systemd user service that runs 'lpf restart'")
def autostart_enable_command():
    autostart.enable()


@autostart_app.command("disable", help="Remove the autostart service (running tunnels keep running)")
def autostart_disable_command():
    autostart.disable()


@autostart_app.command("status", help="Show whether autostart is enabled")
def autostart_status_command():
    autostart.status()


def main():
    """Main entry point for the lpf-cli command."""
    ensure_config_dirs()
    try:
        app()
    finally:
        maybe_notify()


if __name__ == "__main__":
    main()
