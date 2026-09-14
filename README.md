# lpf 🚢

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Built with Typer](https://img.shields.io/badge/built%20with-Typer-009688)](https://typer.tiangolo.com/)
[![Requires autossh](https://img.shields.io/badge/requires-autossh-4EAA25?logo=gnubash&logoColor=white)](https://www.harding.motd.ca/autossh/)
[![Last commit](https://img.shields.io/github/last-commit/mNandhu/lpf-cli)](https://github.com/mNandhu/lpf-cli/commits/master)

`lpf` is a small CLI for managing SSH local port forwards. You add a tunnel
once and it runs in the background, so you don't need to keep a terminal open.
It uses `autossh` underneath, which reconnects the tunnel when the network
drops.

## Quick start

```bash
# install
uv tool install git+https://github.com/mNandhu/lpf-cli.git

# forward localhost:8765 to port 8765 on myserver (any ~/.ssh/config alias works)
lpf add myserver 8765

# see what's running
lpf ls

# if it didn't connect, see why
lpf logs myserver:8765

# remove it
lpf rm myserver:8765
```

You can close the terminal after `lpf add` and the tunnel stays up. Tab
completion is available once you set it up (see
[Shell completion](#shell-completion)).

## Features

- Tunnels run as detached `autossh` processes, so they don't need a tty, tmux,
  or an open terminal.
- `lpf add` waits until the tunnel is actually forwarding. If ssh can't
  connect, it tells you why (an unknown host, a rejected key) instead of
  reporting success.
- If a connection dies, `autossh` notices within about 90 seconds and
  reconnects. It keeps retrying even when the very first connection attempt
  fails.
- Hosts are resolved through your normal SSH setup, so `~/.ssh/config`
  aliases, keys, and `ProxyJump` all work.
- You can add, list, stop, start, and remove tunnels one at a time or all at
  once, and add several ports on the same host in one command.
- `lpf logs` shows each tunnel's autossh and ssh output.
- Tunnel definitions are saved to disk. The processes don't survive a reboot,
  but `lpf restart` brings them back, and `lpf autostart enable` does that
  for you at login.
- `lpf ls` prints a table of tunnels with their status and port mappings.
- Shell completion covers commands, options, your tunnel IDs, and the hosts
  in `~/.ssh/config`.

## Requirements

- `autossh` (for example `apt install autossh` or `brew install autossh`).
  Without it, `lpf` stops with an error that says how to install it.
- Python 3.12+
- SSH access that works without a prompt, meaning a key without a passphrase
  or one loaded in `ssh-agent`. Tunnels run in the background with no terminal
  to type a password into, so `lpf` runs ssh with `BatchMode=yes` and a
  password prompt becomes an error you can read in `lpf logs`.

## Installation

### With `uv tool` (recommended)

`uv tool install` puts `lpf` in its own isolated environment and adds it to
your `PATH`, so it works regardless of which virtualenv is active:

```bash
uv tool install git+https://github.com/mNandhu/lpf-cli.git
```

To upgrade:

```bash
uv tool upgrade lpf-cli
```

To check which version you have:

```bash
lpf --version
```

### Update notifications

Once a day, `lpf` checks the repo's git tags on GitHub for a newer version and, if there is one, prints
a one-line notice after the command's output. The check runs in a separate
background process, so commands never wait on the network, and a failed check
is ignored. It is skipped when output isn't going to a terminal.

To turn it off, set `LPF_NO_UPDATE_CHECK=1` in your environment.

### From a local clone (development)

```bash
uv tool install --editable .
```

### With `pip`

```bash
pip install git+https://github.com/mNandhu/lpf-cli.git
```

## Usage

### Add a tunnel

```bash
lpf add <SSH_HOST> <LOCAL_PORT>... [-r <REMOTE_PORT>] [-H <REMOTE_HOST>] [--force]
```

- `<SSH_HOST>`: SSH host, such as `user@hostname` or an alias from `~/.ssh/config`
- `<LOCAL_PORT>...`: one or more local ports to forward from. Each port becomes its own tunnel.
- `-r, --remote-port`: remote port (defaults to the local port). Only works with a single local port.
- `-H, --remote-host`: host the SSH server forwards to (defaults to `localhost`, meaning the server itself)
- `-f, --force`: remove any existing tunnel on the same local port first

Examples:

```bash
lpf add user@server.com 8080 -r 80
lpf add myserver 8000 8001 8002
```

After starting a tunnel, `add` waits up to 15 seconds for its local port to
start listening. When ssh fails, you see its error right away:

```
Error: Tunnel 'myserver:8080' is not connected: ssh: Could not resolve hostname myserver: Name or service not known
autossh keeps retrying in the background. See 'lpf logs myserver:8080' for details, or remove it with 'lpf rm myserver:8080'.
```

The tunnel stays registered and `autossh` keeps retrying, so it connects on
its own once the host is reachable (after a VPN comes up, for example). If
the host was a typo, remove it with `lpf rm`. `add` exits with status 1 when
any tunnel failed.

If another registered tunnel already uses `<LOCAL_PORT>` (running or stopped),
`add` refuses and tells you which tunnel it is. Pass `-f/--force` to remove
that tunnel first. If the port belongs to a process `lpf` didn't start, `add`
refuses either way, because `--force` only removes tunnels that `lpf` manages.

The SSH server resolves `-H`, not your machine. That means you can reach
anything the server can see, like a container IP or another machine on its
network:

```bash
lpf add user@server.com 3000 -r 3000 -H 172.24.0.2
```

### List tunnels

```bash
lpf ls
```

This shows every configured tunnel with its status and port mapping. The
status is one of:

- `ACTIVE`: `autossh` is running and the local port is listening, which
  means ssh connected and set up the forward.
- `CONNECTING`: `autossh` is running but ssh isn't connected yet, for
  example because the host is down. `lpf logs` shows why.
- `STOPPED`: stopped with `lpf stop`.
- `INACTIVE`: the process is gone, for example after a reboot. `lpf restart`
  starts it again.

### Stop, start, and remove tunnels

`stop`, `start`, and `rm` take a tunnel ID. You can pass it as one
`SSH_HOST:PORT` argument or as two arguments, `SSH_HOST PORT`, the same way
`add` takes them:

```bash
lpf stop user@server.com:8080
lpf stop user@server.com 8080
```

Stop a tunnel:

```bash
lpf stop <TUNNEL_ID>
lpf stop <SSH_HOST> <PORT>
lpf stop --all
```

Start a tunnel:

```bash
lpf start <TUNNEL_ID>
lpf start <SSH_HOST> <PORT>
lpf start --all
```

`start --all` keeps going when one tunnel fails, then exits with status 1.
Like `add`, `start` waits for tunnels to connect. Pass `--no-wait` to return
as soon as the processes are running.

Remove a tunnel:

```bash
lpf rm <TUNNEL_ID>
lpf rm <SSH_HOST> <PORT>
lpf rm --all
```

### Other commands

Restart tunnels that aren't running. Tunnels you stopped on purpose are
skipped unless you pass `--force`, which restarts everything:

```bash
lpf restart [--force] [--no-wait]
```

Sync the saved tunnel state with the processes actually running:

```bash
lpf sync
```

### Logs

Each tunnel keeps a log of `autossh` and `ssh` output from its latest start.
`logs` takes a tunnel ID the same way `stop`, `start`, and `rm` do:

```bash
lpf logs <TUNNEL_ID> [-n <LINES>] [--follow]
lpf logs myserver 8765 -f
```

- `-n, --lines`: how many lines to show from the end (default 50)
- `-f, --follow`: keep printing new lines until you press Ctrl+C

The log is cleared each time the tunnel starts, and deleted by `lpf rm`.
Tunnels started by an older version of `lpf` have no log until they're
restarted.

### Start tunnels at login

On Linux with systemd, `lpf` can install a user service that runs
`lpf restart --no-wait` when you log in:

```bash
lpf autostart enable
lpf autostart status
lpf autostart disable
```

Tunnels you stopped with `lpf stop` stay stopped. The service doesn't wait for
the network, since `autossh` retries until it's up. To start tunnels at boot,
before you log in, also run `loginctl enable-linger`.

The service doesn't run in your shell, so it only sees the environment of the
systemd user manager. If your keys live in `ssh-agent`, make sure that
environment has the right `SSH_AUTH_SOCK` (check with
`systemctl --user show-environment`). `lpf autostart enable` warns when it
differs from your shell's.

Disabling autostart leaves running tunnels alone. On macOS, and on Linux
without systemd, `enable` prints a way to set it up yourself (a launchd agent
or a `@reboot` cron line).

## Shell completion

Install completion for your current shell (bash, zsh, fish, or PowerShell)
once:

```bash
lpf --install-completion
```

Then restart your shell or `source` its rc file. After that, TAB completes
commands and options. `lpf add <TAB>` lists the hosts in your
`~/.ssh/config` (including files it `Include`s), and `lpf rm <TAB>`,
`lpf stop <TAB>`, `lpf start <TAB>`, and `lpf logs <TAB>` list your tunnel
IDs. If you type an `SSH_HOST` first, TAB on the second argument suggests that
host's ports.

If you'd rather not install it, `lpf --show-completion` prints the completion
script so you can read it or source it yourself.

## Development

```bash
uv sync
uv run pytest
```

The tests don't need `autossh` or network access. Set `LPF_CONFIG_DIR` to
keep `lpf` state somewhere other than `~/.config/lpf`, for example to try
changes without touching your real tunnels.
