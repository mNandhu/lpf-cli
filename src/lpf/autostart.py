"""
Start tunnels automatically at login (or boot) with a systemd user service.

The service runs `lpf restart --no-wait` once, which starts every tunnel that
isn't running, skipping ones you stopped on purpose. autossh then keeps
retrying until the network is up, so the service doesn't need to wait for it.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .utils import console

SERVICE_NAME = "lpf.service"


def _unit_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config_home) / "systemd" / "user" / SERVICE_NAME


def _lpf_executable() -> str | None:
    """Absolute path of the lpf command that's running, so the service runs the same install."""
    if os.path.basename(sys.argv[0]) == "lpf" and (found := shutil.which(sys.argv[0])):
        return os.path.abspath(found)
    found = shutil.which("lpf")
    return os.path.abspath(found) if found else None


def service_path_env(lpf_path: str) -> str:
    """A PATH for the service: where lpf, autossh, and ssh live now, plus the system dirs.

    The user manager's default PATH may miss e.g. Homebrew's autossh, but
    copying the whole shell PATH would bake in short-lived entries.
    """
    dirs = [os.path.dirname(lpf_path)]
    for command in ("autossh", "ssh"):
        if found := shutil.which(command):
            dirs.append(os.path.dirname(os.path.abspath(found)))
    dirs += ["/usr/local/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(dirs))


def unit_file_contents(lpf_path: str, path_env: str) -> str:
    return f"""[Unit]
Description=Start lpf SSH tunnels

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart="{lpf_path}" restart --no-wait
# autossh daemonizes into this service's cgroup. Without KillMode=process,
# systemd would kill the tunnels as soon as `lpf restart` exits.
KillMode=process
Environment="PATH={path_env}"
Environment=LPF_NO_UPDATE_CHECK=1

[Install]
WantedBy=default.target
"""


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True
    )


def _require_systemd():
    if platform.system() == "Darwin":
        console.print(
            "[bold red]Error:[/] Autostart isn't supported on macOS yet. You can "
            "create a launchd agent that runs 'lpf restart --no-wait' at login "
            "(RunAtLoad), with a PATH that includes autossh."
        )
        sys.exit(1)
    if shutil.which("systemctl") is None or _systemctl("show-environment").returncode != 0:
        console.print(
            "[bold red]Error:[/] No systemd user session found, so lpf can't "
            "set up autostart. Instead, add this line to 'crontab -e':\n"
            f"@reboot {_lpf_executable() or 'lpf'} restart --no-wait",
            highlight=False,
        )
        sys.exit(1)


def enable():
    _require_systemd()
    lpf_path = _lpf_executable()
    if lpf_path is None:
        console.print("[bold red]Error:[/] Couldn't find the lpf executable on your PATH.")
        sys.exit(1)

    unit_path = _unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit_file_contents(lpf_path, service_path_env(lpf_path)))

    for args in (("daemon-reload",), ("enable", SERVICE_NAME)):
        result = _systemctl(*args)
        if result.returncode != 0:
            console.print(
                f"[bold red]Error:[/] 'systemctl --user {' '.join(args)}' failed: "
                f"{result.stderr.strip()}"
            )
            sys.exit(1)

    console.print(f"[green]Autostart enabled.[/green] Wrote {unit_path}", highlight=False)
    console.print("Tunnels will start when you log in.")
    if not _linger_enabled():
        console.print(
            "To start them at boot, before you log in, run: "
            "[bold]loginctl enable-linger[/bold]"
        )
    if warning := agent_socket_warning(
        os.environ.get("SSH_AUTH_SOCK"), _systemctl("show-environment").stdout
    ):
        console.print(warning, highlight=False)


def agent_socket_warning(shell_socket: str | None, manager_env: str) -> str | None:
    """Warn when the service would talk to a different ssh-agent than this shell.

    ssh stalls or fails to authenticate when SSH_AUTH_SOCK points at the wrong
    (or a dead) agent, and the tunnels then sit at CONNECTING.
    """
    if not shell_socket:
        return None
    manager_socket = next(
        (
            line.split("=", 1)[1]
            for line in manager_env.splitlines()
            if line.startswith("SSH_AUTH_SOCK=")
        ),
        None,
    )
    if manager_socket == shell_socket:
        return None
    return (
        f"[yellow]Warning:[/yellow] Your shell uses the ssh-agent at {shell_socket}, "
        f"but the systemd user manager has SSH_AUTH_SOCK={manager_socket or '(unset)'}. "
        "Tunnels started at login may not find your keys. To fix it, set "
        f"SSH_AUTH_SOCK={shell_socket} in a file under ~/.config/environment.d/, "
        "then log in again."
    )


def disable():
    _require_systemd()
    unit_path = _unit_path()
    if not unit_path.exists():
        console.print("Autostart is not enabled.")
        return

    # Disabling doesn't stop running tunnels (KillMode=process), it only
    # stops them from starting at the next login.
    _systemctl("disable", SERVICE_NAME)
    unit_path.unlink()
    _systemctl("daemon-reload")
    console.print("[green]Autostart disabled.[/green] Running tunnels were left alone.")


def status():
    _require_systemd()
    enabled = _unit_path().exists() and _systemctl("is-enabled", SERVICE_NAME).stdout.strip() == "enabled"
    if not enabled:
        console.print("Autostart is [bold]disabled[/bold]. Enable it with 'lpf autostart enable'.")
        return
    when = "at boot" if _linger_enabled() else "when you log in"
    console.print(f"Autostart is [bold green]enabled[/bold green]. Tunnels start {when}.")


def _linger_enabled() -> bool:
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    try:
        result = subprocess.run(
            ["loginctl", "show-user", user, "--property=Linger", "--value"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.stdout.strip() == "yes"
