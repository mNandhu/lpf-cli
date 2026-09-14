import pytest
import typer

from lpf import autostart, main
from lpf.update_check import _parse_version


@pytest.mark.parametrize(
    "identifier, port, expected",
    [
        ("myserver:8000", None, "myserver:8000"),
        ("myserver", 8000, "myserver:8000"),
        ("user@myserver", 8000, "user@myserver:8000"),
        (None, None, None),
    ],
)
def test_resolve_tunnel_id(identifier, port, expected):
    assert main._resolve_tunnel_id(identifier, port) == expected


def test_resolve_tunnel_id_rejects_id_and_port():
    with pytest.raises(typer.Exit):
        main._resolve_tunnel_id("myserver:8000", 8000)


def test_complete_ssh_host(monkeypatch):
    monkeypatch.setattr(main, "ssh_config_hosts", lambda: ["myserver", "mybox", "other"])
    assert main._complete_ssh_host("my") == ["myserver", "mybox"]


@pytest.mark.parametrize(
    "older, newer",
    [
        ("0.2.0", "0.2.1"),
        ("v0.2.1", "0.10.0"),
        ("0.2.1.dev3+g1eb6778ec", "0.2.1"),
        ("0.2.0", "0.2.1.dev1"),
        ("0.9.9", "1.0.0"),
    ],
)
def test_parse_version_ordering(older, newer):
    assert _parse_version(older) < _parse_version(newer)


def test_parse_version_local_suffix_is_a_release():
    assert _parse_version("0.2.1+d20260914") == _parse_version("0.2.1")
    assert _parse_version("not-a-version") is None


def test_autostart_unit_file():
    unit = autostart.unit_file_contents("/home/me/.local/bin/lpf", "/usr/bin:/bin")
    assert 'ExecStart="/home/me/.local/bin/lpf" restart --no-wait' in unit
    assert "KillMode=process" in unit
    assert 'Environment="PATH=/usr/bin:/bin"' in unit
    assert "WantedBy=default.target" in unit


def test_agent_socket_warning():
    env = "HOME=/home/me\nSSH_AUTH_SOCK=/run/user/1000/openssh_agent\n"
    assert autostart.agent_socket_warning("/run/user/1000/openssh_agent", env) is None
    assert autostart.agent_socket_warning(None, env) is None
    warning = autostart.agent_socket_warning("/run/user/1000/ssh-agent.socket", env)
    assert warning and "SSH_AUTH_SOCK=/run/user/1000/openssh_agent" in warning
    assert "(unset)" in autostart.agent_socket_warning("/tmp/agent", "HOME=/home/me\n")


def test_autostart_service_path(monkeypatch):
    monkeypatch.setattr(
        autostart.shutil,
        "which",
        lambda name: {"autossh": "/opt/homebrew/bin/autossh", "ssh": "/usr/bin/ssh"}.get(name),
    )
    assert autostart.service_path_env("/home/me/.local/bin/lpf") == (
        "/home/me/.local/bin:/opt/homebrew/bin:/usr/bin:/usr/local/bin:/bin"
    )
