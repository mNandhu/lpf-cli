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

# remove it
lpf rm myserver:8765
```

You can close the terminal after `lpf add` and the tunnel stays up. Tab
completion is available once you set it up (see
[Shell completion](#shell-completion)).

## Features

- Tunnels run as detached `autossh` processes, so they don't need a tty, tmux,
  or an open terminal.
- If a connection dies, `autossh` notices within about 90 seconds and
  reconnects. It keeps retrying even when the very first connection attempt
  fails.
- Hosts are resolved through your normal SSH setup, so `~/.ssh/config`
  aliases, keys, and `ProxyJump` all work.
- You can add, list, stop, start, and remove tunnels one at a time or all at
  once.
- Tunnel definitions are saved to disk. The processes don't survive a reboot,
  but `lpf restart` brings them back.
- `lpf sync` cleans up records of tunnel processes that have died.
- `lpf ls` prints a table of tunnels with their status and port mappings.
- Shell completion covers commands, options, and your tunnel IDs.

## Requirements

- `autossh` (for example `apt install autossh` or `brew install autossh`)
- Python 3.12+

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
lpf add <SSH_HOST> <LOCAL_PORT> [-r <REMOTE_PORT>] [-H <REMOTE_HOST>] [--force]
```

- `<SSH_HOST>`: SSH host, such as `user@hostname` or an alias from `~/.ssh/config`
- `<LOCAL_PORT>`: local port to forward from
- `-r, --remote-port`: remote port (defaults to the local port)
- `-H, --remote-host`: host the SSH server forwards to (defaults to `localhost`, meaning the server itself)
- `-f, --force`: remove any existing tunnel on the same local port first

Example:

```bash
lpf add user@server.com 8080 -r 80
```

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

This shows every configured tunnel with its status and port mapping.

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
lpf restart [--force]
```

Sync the saved tunnel state with the processes actually running:

```bash
lpf sync
```

## Shell completion

Install completion for your current shell (bash, zsh, fish, or PowerShell)
once:

```bash
lpf --install-completion
```

Then restart your shell or `source` its rc file. After that, TAB completes
commands and options, and `lpf rm <TAB>`, `lpf stop <TAB>`, and
`lpf start <TAB>` list your tunnel IDs. If you type an `SSH_HOST` first, TAB
on the second argument suggests that host's ports.

If you'd rather not install it, `lpf --show-completion` prints the completion
script so you can read it or source it yourself.
