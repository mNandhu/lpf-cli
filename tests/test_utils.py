import os
import socket

import pytest

from lpf import utils
from lpf.config import STATE_FILE


def test_save_and_load_tunnels_round_trip():
    tunnels = {"myserver:8000": {"local_port": 8000, "remote_port": 80}}
    utils.save_tunnels(tunnels)
    assert utils.load_tunnels() == tunnels
    # The temp file used for the atomic write is gone.
    assert [p.name for p in STATE_FILE.parent.iterdir() if p.is_file()] == [STATE_FILE.name]


def test_failed_save_keeps_the_old_state_file():
    utils.save_tunnels({"a:1": {"local_port": 1}})
    with pytest.raises(TypeError):
        utils.save_tunnels({"b:2": {"local_port": object()}})  # not JSON serializable
    assert utils.load_tunnels() == {"a:1": {"local_port": 1}}
    assert not list(STATE_FILE.parent.glob("*.tmp"))


def test_is_port_in_use_ipv4():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert utils.is_port_in_use(port)
    assert not utils.is_port_in_use(port)


def test_is_port_in_use_sees_wildcard_listener_when_bind_succeeds(monkeypatch):
    # Simulates macOS, where SO_REUSEADDR lets the 127.0.0.1 test bind
    # succeed next to a 0.0.0.0 listener.
    monkeypatch.setattr(utils, "_bind_fails", lambda port: False)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        s.listen()
        port = s.getsockname()[1]
        assert utils.is_port_in_use(port)
    assert not utils.is_port_in_use(port)


def test_is_port_in_use_ipv6_only_listener():
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        s.bind(("::1", 0))
    except OSError:
        pytest.skip("no IPv6 loopback")
    with s:
        s.listen()
        assert utils.is_port_in_use(s.getsockname()[1])


DETAILS = {"ssh_host": "myserver", "local_port": 8000, "remote_port": 80, "remote_host": "localhost"}


@pytest.mark.parametrize(
    "cmdline, expected",
    [
        (["/usr/bin/autossh", "-M", "0", "-N", "-L", "8000:localhost:80", "myserver"], True),
        (["autossh", "-L", "8000:localhost:80", "myserver"], True),
        (["/usr/bin/autossh", "-L", "8001:localhost:80", "myserver"], False),
        (["/usr/bin/autossh", "-L", "8000:localhost:80", "otherserver"], False),
        (["/usr/bin/ssh", "-L", "8000:localhost:80", "myserver"], False),
        ([], False),
    ],
)
def test_cmdline_matches_tunnel(cmdline, expected):
    assert utils.cmdline_matches_tunnel(cmdline, DETAILS) is expected


def test_cmdline_matches_tunnel_saved_before_remote_host_existed():
    legacy = {k: v for k, v in DETAILS.items() if k != "remote_host"}
    assert utils.cmdline_matches_tunnel(["autossh", "-L", "8000:localhost:80", "myserver"], legacy)


@pytest.mark.parametrize("has_proc", [True, False], ids=["proc", "ps"])
def test_process_cmdline_reads_own_process(monkeypatch, has_proc):
    if has_proc and not os.path.exists("/proc/self/cmdline"):
        pytest.skip("no /proc")
    monkeypatch.setattr(utils, "HAS_PROC", has_proc)
    cmdline = utils.process_cmdline(os.getpid())
    assert cmdline and "python" in os.path.basename(cmdline[0])


def test_is_process_running():
    assert utils.is_process_running(os.getpid())
    # Our own PID isn't the autossh process for a tunnel.
    assert not utils.is_process_running(os.getpid(), DETAILS)
    assert not utils.is_process_running(None)
    assert not utils.is_process_running(2**22 + 12345)  # above Linux's pid_max


def test_ssh_config_hosts(tmp_path):
    included = tmp_path / "config.d" / "work"
    included.parent.mkdir()
    included.write_text("Host work-box\n  HostName 10.0.0.5\n")
    config = tmp_path / "config"
    config.write_text(
        f"Include {tmp_path}/config.d/*\n"
        "# Host commented-out\n"
        "Host myserver other\n"
        "  HostName example.com\n"
        "Host=eq-style\n"
        "Host *.internal !bastion jump?\n"
        "host lowercase # trailing comment\n"
        "Match host foo\n"
        "Host myserver\n"
    )
    assert utils.ssh_config_hosts(config) == [
        "work-box",
        "myserver",
        "other",
        "eq-style",
        "lowercase",
    ]


def test_ssh_config_hosts_missing_file(tmp_path):
    assert utils.ssh_config_hosts(tmp_path / "nope") == []


def test_ssh_config_hosts_include_loop(tmp_path):
    config = tmp_path / "config"
    config.write_text(f"Include {config}\nHost a\n")
    assert utils.ssh_config_hosts(config) == ["a"]
