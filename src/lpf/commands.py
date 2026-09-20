"""
CLI command functions for lpf-cli.
"""

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from rich.table import Table

from .utils import (
    console,
    forward_spec,
    is_port_in_use,
    is_process_running,
    load_tunnels,
    log_file_path,
    pid_file_path,
    save_tunnels,
)

# How long to wait for a new tunnel's local port to start listening.
CONNECT_TIMEOUT = 15
# How long to wait for a stopped tunnel to exit and free its port.
STOP_TIMEOUT = 5
# How long autossh gets to write its PID file after forking.
PID_FILE_TIMEOUT = 5

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# Docker's own rule for container names.
CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
# How long to wait for `docker inspect` over ssh.
INSPECT_TIMEOUT = 20

AUTOSSH_MISSING = (
    "[bold red]Error:[/] autossh is not installed (or not on your PATH). "
    "Install it with 'sudo apt install autossh' (Debian/Ubuntu), "
    "'sudo dnf install autossh' (Fedora), or 'brew install autossh' (macOS)."
)


def _wait_until(condition, timeout: float, interval: float = 0.1) -> bool:
    """Poll `condition` until it returns True or `timeout` seconds pass."""
    deadline = time.monotonic() + timeout
    while True:
        if condition():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _spawn_autossh(tunnel_id: str, details: dict) -> int | None:
    """Start the autossh process for a tunnel and return its PID."""
    pid_file = pid_file_path(tunnel_id)
    log_file = log_file_path(tunnel_id)

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
        # Exit (and let autossh retry) when the local port can't be bound,
        # instead of staying connected without a working forward.
        "-o",
        "ExitOnForwardFailure=yes",
        # The process is detached from any terminal, so a password or
        # passphrase prompt could never be answered. Fail instead of hanging.
        "-o",
        "BatchMode=yes",
        # ssh's own errors ("Could not resolve hostname", "Permission denied")
        # go to the same log as autossh's messages.
        "-E",
        str(log_file),
        "-L",
        forward_spec(details),
        details["ssh_host"],
    ]

    env = os.environ.copy()
    env["AUTOSSH_PIDFILE"] = str(pid_file)
    env["AUTOSSH_GATETIME"] = "0"
    env["AUTOSSH_LOGFILE"] = str(log_file)

    # Start each run with a fresh log, so it only holds this run's messages.
    # Replace the file rather than truncating it, so `lpf logs -f` can tell a
    # new run started (the file changes) even if it already wrote a lot.
    log_file.unlink(missing_ok=True)
    log_file.touch()
    pid_file.unlink(missing_ok=True)

    result = subprocess.run(command, env=env, capture_output=True, text=True)

    if result.returncode != 0:
        console.print("[bold red]Error:[/] Failed to start autossh.")
        console.print(f"Stderr: {result.stderr.strip()}")
        return None

    pid = None

    def read_pid() -> bool:
        nonlocal pid
        try:
            pid = int(pid_file.read_text().strip())
            return True
        except (OSError, ValueError):
            return False

    if not _wait_until(read_pid, PID_FILE_TIMEOUT):
        console.print(
            "[bold red]Error:[/] PID file was not created in time. Tunnel may have failed to start."
        )
        return None

    return pid


def _last_ssh_error(tunnel_id: str) -> str | None:
    """The latest ssh message in a tunnel's log, if ssh has exited since it started."""
    try:
        lines = log_file_path(tunnel_id).read_text(errors="replace").splitlines()
    except OSError:
        return None
    if not any("ssh exited with error status" in line for line in lines):
        return None
    # autossh's own lines look like "2026/09/14 13:27:50 autossh[37061]: ...".
    # Everything else came from ssh.
    ssh_lines = [line for line in lines if line.strip() and " autossh[" not in line]
    return ssh_lines[-1] if ssh_lines else "ssh exited with an error"


def _resolve_container_ip(
    ssh_host: str, container: str, network: str | None = None
) -> tuple[str | None, str | None]:
    """Ask the SSH host for a container's IP. Returns (ip, error).

    Prints one "network=ip" line per network, so a container on several
    networks gives separate addresses instead of one run-together string.
    Without `network`, the container's primary network wins (the one it was
    started on), else the first one with an address.
    """
    fmt = "@{{.HostConfig.NetworkMode}}\n{{range $n, $v := .NetworkSettings.Networks}}{{$n}}={{$v.IPAddress}}\n{{end}}"
    remote_cmd = f"docker inspect -f {shlex.quote(fmt)} {shlex.quote(container)}"
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", ssh_host, remote_cmd],
            capture_output=True,
            text=True,
            timeout=INSPECT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out asking {ssh_host} about container '{container}'"
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        return None, (
            f"could not inspect container '{container}' on {ssh_host}: "
            f"{detail[-1] if detail else 'ssh/docker failed'}"
        )
    networks = {}
    mode = None
    for line in result.stdout.splitlines():
        if line.startswith("@"):
            mode = line[1:].strip()
            continue
        net, _, ip = line.strip().partition("=")
        if net and ip:
            networks[net] = ip
    if network is not None:
        if network not in networks:
            have = ", ".join(networks) or "none with an IP"
            return None, f"container '{container}' has no IP on network '{network}' (networks: {have})"
        return networks[network], None
    if not networks:
        return None, (
            f"container '{container}' has no IP address (is it running? "
            f"host-network containers have none)"
        )
    if mode == "default":
        mode = "bridge"
    return networks.get(mode) or next(iter(networks.values())), None


def _refresh_container_hosts(tunnels: dict, tunnel_ids: list[str]) -> list[str]:
    """Re-resolve the IP of every container-backed tunnel. Returns the IDs that failed.

    If a lookup fails but the tunnel has a saved IP, keep using it (with a
    warning) so a brief ssh/docker hiccup doesn't stop a restart.
    """
    ids = [t for t in tunnel_ids if tunnels[t].get("container")]
    if not ids:
        return []

    def lookup(tid):
        d = tunnels[tid]
        return _resolve_container_ip(d["ssh_host"], d["container"], d.get("network"))

    # Lookups are network-bound, so run them together.
    with ThreadPoolExecutor(max_workers=min(8, len(ids))) as pool:
        results = list(pool.map(lookup, ids))

    failed = []
    for tunnel_id, (ip, error) in zip(ids, results):
        details = tunnels[tunnel_id]
        saved = details.get("remote_host")
        if ip is None:
            if saved and saved != "localhost":
                console.print(
                    f"[yellow]Warning:[/] Tunnel '{tunnel_id}': {error}. "
                    f"Using the last known IP {saved}.",
                    highlight=False,
                )
                continue
            console.print(f"[bold red]Error:[/] Tunnel '{tunnel_id}': {error}", highlight=False)
            failed.append(tunnel_id)
            continue
        if ip != saved:
            console.print(f"Container '{details['container']}' is at {ip}.")
        details["remote_host"] = ip
    return failed


def _running_tunnel_on_port(tunnels: dict, local_port: int, exclude_id: str) -> str | None:
    """ID of another running tunnel on this local port, if any."""
    for tid, d in tunnels.items():
        if (
            tid != exclude_id
            and d.get("local_port") == local_port
            and not d.get("stopped")
            and is_process_running(d.get("pid"), d)
        ):
            return tid
    return None


def _start_tunnels(tunnels: dict, tunnel_ids: list[str], wait: bool = True) -> list[str]:
    """Start autossh for each tunnel and save their PIDs.

    With `wait`, also wait for each tunnel's local port to start listening,
    which means ssh connected and set up the forward. Returns the IDs that
    failed. A tunnel that started but didn't connect keeps running, because
    autossh keeps retrying in the background.
    """
    if not tunnel_ids:
        return []
    if shutil.which("autossh") is None:
        console.print(AUTOSSH_MISSING)
        return list(tunnel_ids)

    # Container IPs change when a container is redeployed, so look them up
    # again on every start instead of trusting the saved one.
    failed = _refresh_container_hosts(tunnels, tunnel_ids)
    tunnel_ids = [t for t in tunnel_ids if t not in failed]
    started = []
    for tunnel_id in tunnel_ids:
        details = tunnels[tunnel_id]
        # The connection check below takes a listening port as proof that ssh
        # connected. That only holds if nothing else had the port before we
        # started, e.g. an ssh left behind by a killed autossh, or a program
        # that grabbed the port while the tunnel was stopped.
        holder = _running_tunnel_on_port(tunnels, details["local_port"], tunnel_id)
        if holder is not None:
            console.print(
                f"[bold red]Error:[/] Local port {details['local_port']} is already used by "
                f"running tunnel '{holder}', so tunnel '{tunnel_id}' can't start. "
                f"Stop it first with 'lpf stop {holder}'.",
                highlight=False,
            )
            failed.append(tunnel_id)
            continue
        if is_port_in_use(details["local_port"]):
            console.print(
                f"[bold red]Error:[/] Local port {details['local_port']} is already in use "
                f"by another process, so tunnel '{tunnel_id}' can't start. "
                f"Find it with 'lsof -i :{details['local_port']}'.",
                highlight=False,
            )
            failed.append(tunnel_id)
            continue
        pid = _spawn_autossh(tunnel_id, details)
        if pid is None:
            console.print(f"[bold red]Failed to start tunnel '{tunnel_id}'.[/bold red]")
            failed.append(tunnel_id)
            continue
        details["pid"] = pid
        details["pid_file"] = str(pid_file_path(tunnel_id))
        details.pop("stopped", None)
        started.append(tunnel_id)
    save_tunnels(tunnels)

    if not wait:
        for tunnel_id in started:
            console.print(
                f"[green]Tunnel '{tunnel_id}' started with PID {tunnels[tunnel_id]['pid']}.[/green]"
            )
        return failed

    # Wait for all tunnels at once, so a slow host doesn't hold up the rest.
    pending = set(started)
    errors: dict[str, str] = {}

    def check() -> bool:
        for tunnel_id in list(pending):
            details = tunnels[tunnel_id]
            if is_port_in_use(details["local_port"]):
                pending.discard(tunnel_id)
            elif not is_process_running(details["pid"]):
                errors[tunnel_id] = "autossh exited"
                pending.discard(tunnel_id)
            elif error := _last_ssh_error(tunnel_id):
                errors[tunnel_id] = error
                pending.discard(tunnel_id)
        return not pending

    with console.status("Waiting for tunnels to connect..."):
        _wait_until(check, CONNECT_TIMEOUT, interval=0.2)
    for tunnel_id in pending:
        errors[tunnel_id] = f"still not connected after {CONNECT_TIMEOUT} seconds"

    for tunnel_id in started:
        if tunnel_id in errors:
            failed.append(tunnel_id)
            console.print(
                f"[bold red]Error:[/] Tunnel '{tunnel_id}' is not connected: "
                f"{errors[tunnel_id]}",
                highlight=False,
            )
            console.print(
                f"autossh keeps retrying in the background. See "
                f"'lpf logs {tunnel_id}' for details, or remove it with "
                f"'lpf rm {tunnel_id}'.",
                highlight=False,
            )
        else:
            console.print(
                f"[green]Tunnel '{tunnel_id}' connected (PID {tunnels[tunnel_id]['pid']}).[/green]"
            )
    return failed


def _stop_process(tunnel_id: str, details: dict):
    """Stop a tunnel's autossh process, wait for its port to free up, and clear its PID."""
    pid = details.get("pid")
    if pid and is_process_running(pid, details):
        console.print(f"Stopping tunnel '{tunnel_id}' (PID: {pid})...")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as e:
            console.print(f"[bold red]Error:[/] Failed to stop process {pid}: {e}")
        else:
            # autossh passes SIGTERM on to ssh. Wait for both to let go of the
            # port, so starting the tunnel again right away can bind it.
            _wait_until(
                lambda: not is_process_running(pid)
                and not is_port_in_use(details["local_port"]),
                STOP_TIMEOUT,
            )

    pid_file = details.get("pid_file")
    if pid_file:
        try:
            os.remove(pid_file)
        except FileNotFoundError:
            pass  # It's already gone, which is fine
        except OSError as e:
            console.print(
                f"[bold red]Warning:[/] Could not remove PID file {pid_file}: {e}"
            )
    details.pop("pid", None)
    details.pop("pid_file", None)


def _resolve_name(tunnels: dict, identifier: str) -> str:
    """Resolve a tunnel name to its 'SSH_HOST:PORT' ID; anything else passes through.

    Names can't contain ':' (enforced at `add` time) and every tunnel ID does,
    so a name can never collide with an ID and no precedence check is needed.
    """
    if ":" in identifier:
        return identifier
    for tunnel_id, details in tunnels.items():
        if details.get("name") == identifier:
            return tunnel_id
    return identifier


def _require_tunnel(tunnels: dict, tunnel_id: str) -> dict:
    if tunnel_id not in tunnels:
        console.print(f"[bold red]Error:[/] Tunnel '{tunnel_id}' not found.")
        sys.exit(1)
    return tunnels[tunnel_id]


def _claim_local_port(tunnels: dict, local_port: int, force: bool, tunnel_id: str) -> bool:
    """Check that a local port is free for a new tunnel, removing lpf's own tunnel on it with `force`."""
    # Check lpf's own registered tunnels first: a stopped/inactive tunnel
    # still "owns" its local_port even though nothing is bound to it at the
    # OS level, so is_port_in_use() alone would miss the conflict.
    # A stopped tunnel doesn't hold its port: several tunnels may share one
    # local port as long as only one runs at a time (see _start_tunnels). A
    # stopped tunnel with this exact ID still conflicts, since IDs are unique.
    existing_tunnel_id = next(
        (
            tid
            for tid, d in tunnels.items()
            if d.get("local_port") == local_port
            and (tid == tunnel_id or not d.get("stopped"))
        ),
        None,
    )

    if existing_tunnel_id:
        if not force:
            if existing_tunnel_id == tunnel_id:
                # Same ssh_host and local_port as an existing tunnel: this is a
                # re-add of itself, not a conflict with something else.
                console.print(
                    f"[bold red]Error:[/] Tunnel '{tunnel_id}' already exists. "
                    f"Use --force to restart it, or run 'lpf rm {tunnel_id}' first."
                )
            else:
                console.print(
                    f"[bold red]Error:[/] Local port {local_port} is already assigned to "
                    f"tunnel '{existing_tunnel_id}'. Use --force to replace it, or run "
                    f"'lpf rm {existing_tunnel_id}' first."
                )
            return False
        console.print(
            f"[yellow]Port {local_port} is already assigned to tunnel "
            f"'{existing_tunnel_id}'. Forcing removal.[/yellow]"
        )
        _stop_process(existing_tunnel_id, tunnels[existing_tunnel_id])
        del tunnels[existing_tunnel_id]
        log_file_path(existing_tunnel_id).unlink(missing_ok=True)
        save_tunnels(tunnels)
        console.print(f"[green]Tunnel '{existing_tunnel_id}' removed successfully.[/green]")
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
        return False
    return True


def add_tunnel(
    ssh_host: str,
    local_ports: list[int],
    remote_port: int | None,
    force: bool = False,
    remote_host: str = "localhost",
    name: str | None = None,
    container: str | None = None,
    network: str | None = None,
):
    """Handler for the 'add' command. Adds one tunnel per local port."""
    if container is not None:
        if remote_host != "localhost":
            console.print("[bold red]Error:[/] --container and --remote-host can't be used together.")
            sys.exit(1)
        if not CONTAINER_RE.fullmatch(container):
            console.print("[bold red]Error:[/] Invalid container name.")
            sys.exit(1)
    elif network is not None:
        console.print("[bold red]Error:[/] --network needs --container.")
        sys.exit(1)
    local_ports = list(dict.fromkeys(local_ports))  # drop repeats, keep order
    if remote_port is not None and len(local_ports) > 1:
        console.print(
            "[bold red]Error:[/] --remote-port only works with a single local port. "
            "Add tunnels with different remote ports one at a time."
        )
        sys.exit(1)
    if name is not None and len(local_ports) > 1:
        console.print(
            "[bold red]Error:[/] --name only works with a single local port. "
            "Add tunnels with different names one at a time."
        )
        sys.exit(1)
    if name is not None and not NAME_RE.fullmatch(name):
        console.print(
            "[bold red]Error:[/] --name may only contain letters, digits, '.', '_', and '-'."
        )
        sys.exit(1)

    if shutil.which("autossh") is None:
        console.print(AUTOSSH_MISSING)
        sys.exit(1)

    tunnels = load_tunnels()
    if name is not None:
        owner = next(
            (tid for tid, d in tunnels.items() if d.get("name") == name), None
        )
        this_id = f"{ssh_host}:{local_ports[0]}"
        # A tunnel --force is about to replace (same local port) doesn't count
        # as a conflict: its name is going away along with it.
        port_owner = next(
            (tid for tid, d in tunnels.items() if d.get("local_port") == local_ports[0]),
            None,
        )
        if owner is not None and owner != this_id and not (force and owner == port_owner):
            console.print(
                f"[bold red]Error:[/] Name '{name}' is already used by tunnel '{owner}'."
            )
            sys.exit(1)

    failed = []
    new_ids = []
    for local_port in local_ports:
        tunnel_id = f"{ssh_host}:{local_port}"
        # --force on a tunnel's own ID recreates it (see _claim_local_port), which
        # would otherwise silently drop a name that --name wasn't passed again for.
        kept_name = name if name is not None else tunnels.get(tunnel_id, {}).get("name")
        if not _claim_local_port(tunnels, local_port, force, tunnel_id):
            failed.append(local_port)
            continue
        tunnels[tunnel_id] = {
            "local_port": local_port,
            # If remote_port isn't specified, it defaults to local_port
            "remote_port": remote_port or local_port,
            "ssh_host": ssh_host,
            # Resolved on the SSH server, not here: "localhost" means the server
            # itself, anything else is a host the server can reach (a container IP,
            # another machine on its network).
            "remote_host": remote_host,
            **({"name": kept_name} if kept_name is not None else {}),
            **({"container": container} if container else {}),
            **({"network": network} if network else {}),
        }
        new_ids.append(tunnel_id)

    failed_ids = _start_tunnels(tunnels, new_ids)
    # A tunnel whose autossh never started isn't worth keeping. One that
    # started but hasn't connected stays, since autossh keeps retrying.
    unstarted = [tid for tid in failed_ids if "pid" not in tunnels[tid]]
    if unstarted:
        for tunnel_id in unstarted:
            del tunnels[tunnel_id]
        save_tunnels(tunnels)

    if failed or failed_ids:
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
    table.add_column("STATUS", justify="center", no_wrap=True, min_width=11)
    table.add_column("FORWARDING", style="yellow", no_wrap=True, min_width=30)
    table.add_column("NAME", style="magenta", no_wrap=True)

    for tunnel_id, details in sorted(tunnels.items()):
        table.add_row(
            tunnel_id, _status(details), _forwarding(details), details.get("name", "")
        )

    console.print(table)


def _status(details: dict) -> str:
    if details.get("stopped"):
        return "[yellow]STOPPED[/yellow]"
    if not is_process_running(details.get("pid"), details):
        return "[red]INACTIVE[/red]"
    # autossh being alive doesn't mean ssh is connected: it may be retrying a
    # host that's down. ssh only binds the local port once the forward is up.
    if is_port_in_use(details["local_port"]):
        return "[green]ACTIVE[/green]"
    return "[blue]CONNECTING[/blue]"


def _forwarding(details: dict) -> str:
    remote_host = details.get("remote_host") or "localhost"
    target = f"{details['container']}({remote_host})" if details.get("container") else remote_host
    return f"localhost:{details['local_port']} -> {target}:{details['remote_port']}"


def remove_tunnel(tunnel_id: str):
    """Handler for the 'rm' command."""
    tunnels = load_tunnels()
    tunnel_id = _resolve_name(tunnels, tunnel_id)
    details = _require_tunnel(tunnels, tunnel_id)

    _stop_process(tunnel_id, details)
    log_file_path(tunnel_id).unlink(missing_ok=True)

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
    tunnel_id = _resolve_name(tunnels, tunnel_id)
    details = _require_tunnel(tunnels, tunnel_id)

    if details.get("stopped"):
        console.print(f"[yellow]Tunnel '{tunnel_id}' is already stopped.[/yellow]")
        return

    _stop_process(tunnel_id, details)
    details["stopped"] = True
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


def start_tunnel(tunnel_id: str, wait: bool = True):
    """Handler for the 'start' command - starts a stopped or inactive tunnel."""
    tunnels = load_tunnels()
    tunnel_id = _resolve_name(tunnels, tunnel_id)
    details = _require_tunnel(tunnels, tunnel_id)

    if not details.get("stopped") and is_process_running(details.get("pid"), details):
        console.print(f"[yellow]Tunnel '{tunnel_id}' is already running.[/yellow]")
        return

    if _start_tunnels(tunnels, [tunnel_id], wait):
        sys.exit(1)


def start_all_tunnels(wait: bool = True):
    """Handler for 'start --all'."""
    tunnels = load_tunnels()
    if not tunnels:
        console.print("No tunnels configured.")
        return

    to_start = []
    for tunnel_id, details in tunnels.items():
        if not details.get("stopped") and is_process_running(details.get("pid"), details):
            console.print(f"[yellow]Tunnel '{tunnel_id}' is already running.[/yellow]")
        else:
            to_start.append(tunnel_id)

    if to_start:
        console.print(f"Starting {len(to_start)} tunnel(s)...")
    # One failing tunnel doesn't stop the others from starting.
    failed = _start_tunnels(tunnels, to_start, wait)
    if failed:
        console.print(
            f"[bold red]{len(failed)} of {len(to_start)} tunnel(s) failed to start.[/bold red]"
        )
        sys.exit(1)


def restart_tunnels(force: bool = False, wait: bool = True):
    """Finds all inactive tunnels and restarts them. With --force, restarts all (including stopped)."""
    sync_tunnels(silent=True)
    tunnels = load_tunnels()

    if force:
        console.print("Forcing restart of all tunnels...")
    else:
        console.print("Checking for inactive tunnels to restart...")

    to_start = []
    for tunnel_id, details in tunnels.items():
        # Skip intentionally stopped tunnels unless forcing
        if details.get("stopped") and not force:
            continue

        is_running = is_process_running(details.get("pid"), details)
        if force and is_running:
            _stop_process(tunnel_id, details)
        if force or not is_running:
            to_start.append(tunnel_id)

    if not to_start:
        console.print("[green]No tunnels needed restarting.[/green]")
        return

    failed = _start_tunnels(tunnels, to_start, wait)
    restarted_count = len(to_start) - len(failed)
    console.print(f"[green]Finished. Restarted {restarted_count} tunnel(s).[/green]")
    if failed:
        console.print(
            f"[bold red]{len(failed)} tunnel(s) failed to restart.[/bold red]"
        )
        sys.exit(1)


def sync_tunnels(silent: bool = False):
    """Syncs the state of tunnels, cleaning up stale entries."""
    tunnels = load_tunnels()
    if not tunnels:
        if not silent:
            console.print("No tunnels to sync.")
        return

    stale_count = 0
    with console.status("[bold green]Syncing tunnel states...[/]"):
        for tunnel_id, details in tunnels.items():
            if details.get("stopped"):
                continue
            pid = details.get("pid")
            if pid and not is_process_running(pid, details):
                if not silent:
                    console.print(
                        f"[yellow]Stale PID found for tunnel '{tunnel_id}'. Cleaning up.[/yellow]"
                    )
                # Remove stale PID info from the original dict
                details.pop("pid", None)
                details.pop("pid_file", None)
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


def show_logs(tunnel_id: str, lines: int = 50, follow: bool = False):
    """Handler for the 'logs' command: print the end of a tunnel's autossh/ssh log."""
    tunnels = load_tunnels()
    tunnel_id = _resolve_name(tunnels, tunnel_id)
    _require_tunnel(tunnels, tunnel_id)

    log_file = log_file_path(tunnel_id)
    if not log_file.exists():
        console.print(
            f"No logs for '{tunnel_id}' yet. Logs are written from the next time "
            f"the tunnel starts ('lpf start {tunnel_id}' or 'lpf restart --force')."
        )
        return

    f = open(log_file, "r", errors="replace")
    try:
        tail = f.read().splitlines()[-lines:] if lines > 0 else []
        for line in tail:
            console.print(line, markup=False, highlight=False, soft_wrap=True)
        if not follow:
            return

        while True:
            chunk = f.read()
            if chunk:
                console.print(chunk, end="", markup=False, highlight=False, soft_wrap=True)
                continue
            # Each start replaces the log with a new file. Once that happens,
            # switch to the new file and print it from the top.
            try:
                current = log_file.stat()
            except FileNotFoundError:
                current = None  # removed, or about to be recreated
            if current and (
                current.st_ino != os.fstat(f.fileno()).st_ino
                or current.st_size < f.tell()
            ):
                f.close()
                f = open(log_file, "r", errors="replace")
                continue
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        f.close()
