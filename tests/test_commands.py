import itertools
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from lpf import commands
from lpf.main import app
from lpf.utils import load_tunnels, log_file_path, save_tunnels

runner = CliRunner()


def lpf(*args):
    return runner.invoke(app, list(args))


@pytest.fixture
def fake(monkeypatch):
    """Replace autossh and the OS checks with an in-memory stand-in.

    By default every tunnel starts and connects. Tests put tunnel IDs in
    `spawn_fails` or `ssh_errors` to make them fail, or set `connect` to False
    to leave them connecting.
    """
    # PIDs above Linux's pid_max, so a stray os.kill can't hit a real process.
    pids = itertools.count(5_000_000)
    state = SimpleNamespace(
        running={},  # pid -> local_port
        listening=set(),  # local ports
        spawned=[],
        killed=[],
        spawn_fails=set(),
        ssh_errors={},
        connect=True,
    )

    def spawn(tunnel_id, details):
        state.spawned.append(tunnel_id)
        if tunnel_id in state.spawn_fails:
            return None
        pid = next(pids)
        state.running[pid] = details["local_port"]
        if state.connect and tunnel_id not in state.ssh_errors:
            state.listening.add(details["local_port"])
        return pid

    def kill(pid, sig):
        state.killed.append(pid)
        state.listening.discard(state.running.pop(pid))

    monkeypatch.setattr(commands.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(commands, "_spawn_autossh", spawn)
    monkeypatch.setattr(commands, "is_process_running", lambda pid, details=None: pid in state.running)
    monkeypatch.setattr(commands, "is_port_in_use", lambda port: port in state.listening)
    monkeypatch.setattr(commands, "_last_ssh_error", lambda tid: state.ssh_errors.get(tid))
    monkeypatch.setattr(commands.os, "kill", kill)
    monkeypatch.setattr(commands, "CONNECT_TIMEOUT", 0.3)
    monkeypatch.setattr(commands, "STOP_TIMEOUT", 0.3)
    return state


def add_saved_tunnel(tunnel_id, local_port, **extra):
    tunnels = load_tunnels()
    tunnels[tunnel_id] = {
        "local_port": local_port,
        "remote_port": local_port,
        "ssh_host": tunnel_id.rsplit(":", 1)[0],
        "remote_host": "localhost",
        **extra,
    }
    save_tunnels(tunnels)


def test_add_starts_and_saves_tunnel(fake):
    result = lpf("add", "myserver", "8000", "-r", "80")
    assert result.exit_code == 0, result.output
    assert "Tunnel 'myserver:8000' connected" in result.output
    saved = load_tunnels()["myserver:8000"]
    assert saved["remote_port"] == 80
    assert saved["pid"] in fake.running


def test_add_multiple_ports(fake):
    result = lpf("add", "myserver", "8000", "8001", "8000")
    assert result.exit_code == 0, result.output
    assert fake.spawned == ["myserver:8000", "myserver:8001"]
    assert load_tunnels()["myserver:8001"]["remote_port"] == 8001


def test_add_multiple_ports_with_remote_port_is_an_error(fake):
    result = lpf("add", "myserver", "8000", "8001", "-r", "80")
    assert result.exit_code == 1
    assert "--remote-port only works with a single local port" in result.output
    assert fake.spawned == []


def test_add_refuses_port_owned_by_another_tunnel(fake):
    add_saved_tunnel("old:8000", 8000, stopped=True)
    result = lpf("add", "myserver", "8000")
    assert result.exit_code == 1
    assert "already assigned to tunnel 'old:8000'" in result.output
    assert set(load_tunnels()) == {"old:8000"}


def test_add_refuses_duplicate_of_same_tunnel(fake):
    add_saved_tunnel("myserver:8000", 8000, stopped=True)
    result = lpf("add", "myserver", "8000")
    assert result.exit_code == 1
    assert "Tunnel 'myserver:8000' already exists" in result.output
    assert "already assigned to tunnel" not in result.output
    assert set(load_tunnels()) == {"myserver:8000"}


def test_add_force_replaces_running_tunnel(fake):
    lpf("add", "old", "8000")
    old_pid = load_tunnels()["old:8000"]["pid"]
    result = lpf("add", "myserver", "8000", "--force")
    assert result.exit_code == 0, result.output
    assert fake.killed == [old_pid]
    assert set(load_tunnels()) == {"myserver:8000"}


def test_add_refuses_port_used_by_external_process(fake):
    fake.listening.add(8000)
    result = lpf("add", "myserver", "8000", "--force")
    assert result.exit_code == 1
    assert "in use by an external process" in result.output
    assert fake.spawned == []


def test_add_reports_ssh_failure_and_keeps_retrying_tunnel(fake):
    fake.ssh_errors["nohost:8000"] = "ssh: Could not resolve hostname nohost: Name or service not known"
    result = lpf("add", "nohost", "8000")
    assert result.exit_code == 1
    assert "is not connected: ssh: Could not resolve hostname nohost" in result.output
    assert "lpf logs nohost:8000" in result.output
    assert "connected (PID" not in result.output
    # autossh keeps retrying, so the tunnel stays registered.
    assert "pid" in load_tunnels()["nohost:8000"]


def test_add_reports_timeout_when_never_connecting(fake):
    fake.connect = False
    result = lpf("add", "slow", "8000")
    assert result.exit_code == 1
    assert "still not connected after" in result.output


def test_add_drops_tunnel_whose_autossh_did_not_start(fake):
    fake.spawn_fails.add("myserver:8000")
    result = lpf("add", "myserver", "8000")
    assert result.exit_code == 1
    assert load_tunnels() == {}


def test_add_continues_past_a_conflicting_port(fake):
    add_saved_tunnel("old:8000", 8000, stopped=True)
    result = lpf("add", "myserver", "8000", "8001")
    assert result.exit_code == 1
    assert set(load_tunnels()) == {"old:8000", "myserver:8001"}


def test_missing_autossh_gives_install_hint(fake, monkeypatch):
    monkeypatch.setattr(commands.shutil, "which", lambda name: None)
    add_saved_tunnel("myserver:8000", 8000, stopped=True)
    for args in (["add", "myserver", "8001"], ["start", "myserver:8000"], ["restart", "--force"]):
        result = lpf(*args)
        assert result.exit_code == 1, args
        assert "autossh is not installed" in result.output
        assert isinstance(result.exception, SystemExit)
    assert fake.spawned == []


def test_start_all_continues_after_a_failure(fake):
    add_saved_tunnel("a:8000", 8000, stopped=True)
    add_saved_tunnel("b:8001", 8001, stopped=True)
    fake.spawn_fails.add("a:8000")
    result = lpf("start", "--all")
    assert result.exit_code == 1
    assert fake.spawned == ["a:8000", "b:8001"]
    assert "1 of 2 tunnel(s) failed to start" in result.output
    tunnels = load_tunnels()
    assert tunnels["a:8000"]["stopped"]
    assert "stopped" not in tunnels["b:8001"]


def test_start_no_wait_skips_connection_check(fake):
    fake.connect = False
    add_saved_tunnel("a:8000", 8000, stopped=True)
    result = lpf("start", "a:8000", "--no-wait")
    assert result.exit_code == 0, result.output
    assert "started with PID" in result.output


def test_ls_statuses(fake):
    lpf("add", "active", "8000")
    fake.connect = False
    lpf("add", "connecting", "8001")
    add_saved_tunnel("stopped:8002", 8002, stopped=True)
    add_saved_tunnel("dead:8003", 8003, pid=4_999_999)
    output = lpf("ls").output
    rows = {line.split()[0]: line.split()[1] for line in output.splitlines()[1:] if line.strip()}
    assert rows == {
        "active:8000": "ACTIVE",
        "connecting:8001": "CONNECTING",
        "dead:8003": "INACTIVE",
        "stopped:8002": "STOPPED",
    }


def test_add_with_name_persists_it(fake):
    result = lpf("add", "host1", "3000", "--name", "host1_grafana")
    assert result.exit_code == 0, result.output
    assert load_tunnels()["host1:3000"]["name"] == "host1_grafana"


def test_add_name_with_multiple_ports_is_an_error(fake):
    result = lpf("add", "myserver", "8000", "8001", "--name", "mine")
    assert result.exit_code == 1
    assert "--name only works with a single local port" in result.output
    assert fake.spawned == []


def test_add_rejects_invalid_name(fake):
    result = lpf("add", "myserver", "8000", "--name", "bad name!")
    assert result.exit_code == 1
    assert "may only contain letters, digits" in result.output
    assert fake.spawned == []


def test_add_rejects_trailing_newline_in_name(fake):
    result = lpf("add", "myserver", "8000", "--name", "grafana\n")
    assert result.exit_code == 1
    assert "may only contain letters, digits" in result.output
    assert fake.spawned == []


def test_add_rejects_duplicate_name(fake):
    lpf("add", "host1", "3000", "--name", "grafana")
    result = lpf("add", "host2", "4000", "--name", "grafana")
    assert result.exit_code == 1
    assert "Name 'grafana' is already used by tunnel 'host1:3000'" in result.output
    assert fake.spawned == ["host1:3000"]


def test_start_stop_rm_logs_resolve_by_name(fake):
    lpf("add", "host1", "3000", "--name", "grafana")

    result = lpf("stop", "grafana")
    assert result.exit_code == 0, result.output
    assert load_tunnels()["host1:3000"]["stopped"]

    result = lpf("start", "grafana")
    assert result.exit_code == 0, result.output
    assert "pid" in load_tunnels()["host1:3000"]

    result = lpf("logs", "grafana")
    assert result.exit_code == 0, result.output

    result = lpf("rm", "grafana")
    assert result.exit_code == 0, result.output
    assert load_tunnels() == {}


def test_ls_shows_name(fake, monkeypatch):
    monkeypatch.setenv("COLUMNS", "120")
    lpf("add", "host1", "3000", "--name", "host1_grafana_anything_xyz")
    output = lpf("ls").output
    assert "host1_grafana_anything_xyz" in output
    # STATUS must never be squeezed to make room for a long NAME.
    rows = {line.split()[0]: line.split()[1] for line in output.splitlines()[1:] if line.strip()}
    assert rows["host1:3000"] == "ACTIVE"


def test_add_force_replacing_named_tunnel_reuses_its_name(fake):
    lpf("add", "host1", "3000", "--name", "grafana")
    result = lpf("add", "host2", "3000", "--name", "grafana", "--force")
    assert result.exit_code == 0, result.output
    assert load_tunnels()["host2:3000"]["name"] == "grafana"
    assert set(load_tunnels()) == {"host2:3000"}


def test_add_force_self_readd_keeps_name(fake):
    lpf("add", "host1", "3000", "--name", "grafana")
    result = lpf("add", "host1", "3000", "--force")
    assert result.exit_code == 0, result.output
    assert load_tunnels()["host1:3000"]["name"] == "grafana"


def test_restart_force_stops_then_starts(fake):
    lpf("add", "myserver", "8000")
    old_pid = load_tunnels()["myserver:8000"]["pid"]
    result = lpf("restart", "--force")
    assert result.exit_code == 0, result.output
    assert fake.killed == [old_pid]
    assert load_tunnels()["myserver:8000"]["pid"] != old_pid


def test_start_refuses_port_taken_while_stopped(fake):
    add_saved_tunnel("a:8000", 8000, stopped=True)
    fake.listening.add(8000)  # some other program has the port now
    result = lpf("start", "a:8000")
    assert result.exit_code == 1
    assert "Local port 8000 is already in use by another process" in result.output
    assert "connected" not in result.output
    assert fake.spawned == []


def test_restart_force_does_not_report_leftover_port_as_connected(fake, monkeypatch):
    lpf("add", "myserver", "8000")

    def kill_leaving_ssh_behind(pid, sig):
        fake.killed.append(pid)
        del fake.running[pid]  # autossh dies, its ssh keeps the port

    monkeypatch.setattr(commands.os, "kill", kill_leaving_ssh_behind)
    result = lpf("restart", "--force")
    assert result.exit_code == 1
    assert "already in use by another process" in result.output
    assert "connected (PID" not in result.output
    assert len(fake.spawned) == 1


def test_logs_follow_switches_to_new_log_after_restart(fake, monkeypatch, capsys):
    add_saved_tunnel("myserver:8000", 8000)
    log_file = log_file_path("myserver:8000")
    log_file.write_text("old run\n")
    sleeps = 0

    def fake_sleep(seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            # A restart replaces the log with a longer one before the next poll.
            log_file.unlink()
            log_file.write_text("new run: ssh error\n" * 20)
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr(commands.time, "sleep", fake_sleep)
    commands.show_logs("myserver:8000", lines=50, follow=True)
    output = capsys.readouterr().out.splitlines()
    assert output == ["old run"] + ["new run: ssh error"] * 20


def test_rm_deletes_log_file(fake):
    lpf("add", "myserver", "8000")
    log_file_path("myserver:8000").write_text("some log\n")
    result = lpf("rm", "myserver", "8000")
    assert result.exit_code == 0, result.output
    assert not log_file_path("myserver:8000").exists()
    assert load_tunnels() == {}


def test_logs_shows_last_lines(fake):
    add_saved_tunnel("myserver:8000", 8000)
    log_file_path("myserver:8000").write_text("".join(f"line [{i}]\n" for i in range(10)))
    result = lpf("logs", "myserver", "8000", "-n", "3")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["line [7]", "line [8]", "line [9]"]


def test_logs_without_log_file(fake):
    add_saved_tunnel("myserver:8000", 8000)
    result = lpf("logs", "myserver:8000")
    assert result.exit_code == 0
    assert "No logs for 'myserver:8000' yet" in result.output


def test_logs_unknown_tunnel(fake):
    result = lpf("logs", "nope:1")
    assert result.exit_code == 1
    assert "not found" in result.output


def test_last_ssh_error_reads_ssh_message_from_log():
    log_file_path("x:1").write_text(
        "2026/09/14 13:27:50 autossh[37061]: starting ssh (count 1)\n"
        "ssh: Could not resolve hostname x: Name or service not known\n"
        "2026/09/14 13:27:50 autossh[37061]: ssh exited with error status 255; restarting ssh\n"
    )
    assert commands._last_ssh_error("x:1") == "ssh: Could not resolve hostname x: Name or service not known"


def test_last_ssh_error_ignores_log_without_ssh_exit():
    log_file_path("x:1").write_text("2026/09/14 13:27:50 autossh[37061]: starting ssh (count 1)\n")
    assert commands._last_ssh_error("x:1") is None
    assert commands._last_ssh_error("missing:1") is None


def fake_inspect(monkeypatch, stdout="", returncode=0, stderr=""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)

    monkeypatch.setattr(commands.subprocess, "run", run)
    return calls


def test_add_container_resolves_ip(fake, monkeypatch):
    calls = fake_inspect(monkeypatch, "@net_b\nnet_a=10.89.0.3\nnet_b=10.89.1.17\n")
    result = lpf("add", "vm", "3000", "-c", "grafana")
    assert result.exit_code == 0, result.output
    saved = load_tunnels()["vm:3000"]
    assert saved["remote_host"] == "10.89.1.17"  # primary network, not alphabetical first
    assert saved["container"] == "grafana"
    assert calls[0][:4] == ["ssh", "-o", "BatchMode=yes", "vm"]


def test_container_network_choice(fake, monkeypatch):
    fake_inspect(monkeypatch, "net_a=10.89.0.3\nnet_b=10.89.1.17\n")
    assert lpf("add", "vm", "3000", "-c", "grafana", "--network", "net_b").exit_code == 0
    assert load_tunnels()["vm:3000"]["remote_host"] == "10.89.1.17"


def test_restart_refreshes_container_ip(fake, monkeypatch):
    fake_inspect(monkeypatch, "n=10.0.0.5\n")
    add_saved_tunnel("vm:3000", 3000, container="grafana", remote_host="10.0.0.1")
    result = lpf("start", "vm:3000")
    assert result.exit_code == 0, result.output
    assert load_tunnels()["vm:3000"]["remote_host"] == "10.0.0.5"


def test_container_inspect_failure(fake, monkeypatch):
    fake_inspect(monkeypatch, returncode=1, stderr="Error: No such object: nope\n")
    result = lpf("add", "vm", "3000", "-c", "nope")
    assert result.exit_code == 1
    assert "No such object" in result.output
    assert "vm:3000" not in load_tunnels()


def test_container_conflicts_with_remote_host(fake):
    assert lpf("add", "vm", "3000", "-c", "g", "-H", "1.2.3.4").exit_code == 1


def test_lookup_failure_falls_back_to_saved_ip(fake, monkeypatch):
    fake_inspect(monkeypatch, returncode=255, stderr="ssh: timeout\n")
    add_saved_tunnel("vm:3000", 3000, container="grafana", remote_host="10.0.0.1")
    result = lpf("start", "vm:3000")
    assert result.exit_code == 0, result.output
    assert "last known IP 10.0.0.1" in result.output
