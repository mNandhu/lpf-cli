"""
Utility functions for lpf-cli.
"""

import errno
import glob
import json
import os
import re
import shlex
import socket
import subprocess
from pathlib import Path

from rich.console import Console

from .config import CONFIG_DIR, LOG_DIR, PID_DIR, STATE_FILE

console = Console()

# Linux exposes process command lines under /proc; macOS doesn't.
HAS_PROC = os.path.exists("/proc/self/cmdline")


def ensure_config_dirs():
    """Ensure the configuration, PID, and log directories exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PID_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)


def write_json_atomic(path: Path, data, indent: int | None = None):
    """Write JSON to a temp file and rename it over `path`.

    A crash or a full disk mid-write then leaves the old file intact instead of
    a truncated one.
    """
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=indent)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


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
    write_json_atomic(STATE_FILE, tunnels, indent=2)


def sanitize_filename(name):
    """Replace special characters in a string to make it a valid filename."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name)


def pid_file_path(tunnel_id: str) -> Path:
    return PID_DIR / f"{sanitize_filename(tunnel_id)}.pid"


def log_file_path(tunnel_id: str) -> Path:
    return LOG_DIR / f"{sanitize_filename(tunnel_id)}.log"


LOOPBACKS = ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1"))


def is_port_in_use(port: int) -> bool:
    """Check whether something is listening on a local port.

    ssh binds a -L forward on both 127.0.0.1 and ::1, so a listener on either
    one counts. SO_REUSEADDR matches what ssh sets, so connections from a
    tunnel that just closed (TIME_WAIT) don't count as the port being in use.
    """
    # On macOS and the BSDs, SO_REUSEADDR lets the test bind to 127.0.0.1
    # succeed even while another program listens on 0.0.0.0, so a bind alone
    # can miss it. That listener does accept connections, though.
    return _bind_fails(port) or _accepts_connections(port)


def _accepts_connections(port: int) -> bool:
    for family, address in LOOPBACKS:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex((address, port)) == 0:
                    return True
        except OSError:
            pass
    return False


def _bind_fails(port: int) -> bool:
    for family, address in LOOPBACKS:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((address, port))
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                return True
            # Anything else (no IPv6 loopback, a privileged port) says nothing
            # about another listener. If ssh can't bind either, the tunnel's
            # startup check reports it.
    return False


def forward_spec(details: dict) -> str:
    """Build the autossh -L argument for a tunnel.

    remote_host defaults to "localhost" so tunnels saved before -H existed keep
    producing the string their running process was started with -- otherwise
    is_process_running would read every one of them as a stale PID.
    """
    remote_host = details.get("remote_host") or "localhost"
    return f"{details['local_port']}:{remote_host}:{details['remote_port']}"


def process_cmdline(pid: int) -> list[str] | None:
    """Return a process's arguments, or None if it can't be read."""
    if HAS_PROC:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read()
        except OSError:
            return None
        # Arguments are NUL-separated. A zombie has an empty cmdline.
        return raw.decode(errors="replace").rstrip("\0").split("\0") if raw else None

    # macOS and other Unixes have no /proc, so ask ps.
    try:
        result = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # ps joins arguments with spaces. The arguments we match on (the -L spec
    # and the SSH host) can't contain spaces, so splitting them back is safe.
    return result.stdout.split()


def cmdline_matches_tunnel(cmdline: list[str], tunnel_details: dict) -> bool:
    """Check that a command line is the autossh process for this tunnel."""
    return (
        bool(cmdline)
        and "autossh" in os.path.basename(cmdline[0])
        and forward_spec(tunnel_details) in cmdline
        and tunnel_details["ssh_host"] in cmdline
    )


def is_process_running(pid, tunnel_details=None):
    """
    Check if a process with the given PID is running.
    If tunnel_details are provided, also verify it's the correct autossh process,
    so a PID reused by an unrelated process doesn't look like a live tunnel.
    """
    if pid is None:
        return False
    try:
        # os.kill(pid, 0) doesn't send a signal, but checks for process existence.
        os.kill(pid, 0)
    except OSError:
        return False

    if tunnel_details is None:
        return True

    cmdline = process_cmdline(pid)
    return cmdline is not None and cmdline_matches_tunnel(cmdline, tunnel_details)


def ssh_config_hosts(config_path: Path | None = None) -> list[str]:
    """Host aliases from ~/.ssh/config and the files it Includes.

    Wildcard patterns like `Host *` or `Host !bastion` aren't hosts you can
    connect to, so they're left out.
    """
    ssh_dir = Path.home() / ".ssh"
    hosts: dict[str, None] = {}  # insertion-ordered set
    seen_files: set[Path] = set()

    def read(path: Path):
        try:
            path = path.resolve()
            if path in seen_files:
                return
            seen_files.add(path)
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            return
        for line in lines:
            match = re.match(r"\s*(\w+)(?:\s*=\s*|\s+)(.*)", line)
            if not match or line.lstrip().startswith("#"):
                continue
            keyword, value = match.group(1).lower(), match.group(2)
            try:
                args = shlex.split(value, comments=True)
            except ValueError:
                continue
            if keyword == "host":
                for host in args:
                    if not any(c in host for c in "*?!"):
                        hosts[host] = None
            elif keyword == "include":
                for pattern in args:
                    # Relative Include paths are relative to ~/.ssh.
                    pattern = os.path.expanduser(pattern)
                    if not os.path.isabs(pattern):
                        pattern = str(ssh_dir / pattern)
                    for included in sorted(glob.glob(pattern)):
                        read(Path(included))

    read(config_path or ssh_dir / "config")
    return list(hosts)
