# lpf-cli

A lightweight CLI tool for managing SSH port forwarding tunnels with `autossh`. Create, control, and monitor persistent local port forwarding with ease.

## Features

- **Simple tunnel management:** Add, list, start, stop, and remove SSH tunnels
- **Persistent state:** Tunnels survive shell restarts and system reboots
- **Lifecycle control:** Manage individual tunnels or batch operations
- **System sync:** Auto-cleanup of stale tunnel processes
- **Rich output:** Clear, formatted terminal display
- **Autocompletion:** Shell completion for tunnel IDs and commands

## Requirements

- `autossh` installed on your system
- Python 3.12+

## Installation

### Using `uv` (recommended)

```bash
uv pip install .
```

For development:

```bash
uv pip install -e .
```

### Using `pip`

```bash
pip install .
```

## Usage

### Add a tunnel

```bash
lpf add <SSH_HOST> <LOCAL_PORT> [-r <REMOTE_PORT>] [--force]
```

- `<SSH_HOST>`: SSH host (e.g., `user@hostname`)
- `<LOCAL_PORT>`: Local port to forward from
- `-r, --remote-port`: Remote port (defaults to local port)
- `-H, --remote-host`: Host the SSH server forwards to (defaults to `localhost`, i.e. the server itself)
- `-f, --force`: Remove any existing tunnel on the same local port

Example:
```bash
lpf add user@server.com 8080 -r 80
```

If `<LOCAL_PORT>` is already claimed by another registered tunnel (running or
stopped), `add` refuses and names the conflicting tunnel; pass `-f/--force` to
remove that tunnel first. If the port is held by some other, non-`lpf`
process instead, `add` refuses outright -- `--force` can't kill what it
doesn't manage.

`-H` is resolved **on the SSH server**, so it reaches anything the server can
see but does not itself listen on -- a container IP, another machine on its
network:

```bash
lpf add user@server.com 3000 -r 3000 -H 172.24.0.2
```

### List tunnels

```bash
lpf ls
```

Displays table of all configured tunnels with status and port mappings.

### Control tunnels

`stop`, `start`, and `rm` all take a tunnel ID, which can be given either as
one `SSH_HOST:PORT` argument or as two separate arguments -- `SSH_HOST PORT`
-- mirroring how `add` takes them:

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

Restart all tunnels:
```bash
lpf restart [--force]
```

Sync tunnel state with system:
```bash
lpf sync
```

## Shell completion

Install completion for your current shell (bash, zsh, fish, PowerShell) once:

```bash
lpf --install-completion
```

Restart your shell (or `source` its rc file) and TAB-completion is live for
commands, options, and `lpf`'s own data -- `lpf rm <TAB>`, `lpf stop <TAB>`,
and `lpf start <TAB>` list your registered tunnel IDs; typing an `SSH_HOST`
first and then TAB on the second argument completes to that host's known
ports.

Don't want to install it system-wide? `lpf --show-completion` prints the
completion script so you can inspect it or source it manually.
