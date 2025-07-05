# lpf-cli

A CLI tool to manage local port forwarding tunnels with `autossh`.

## Features

- Add and start new SSH tunnels.
- List all configured tunnels and their status.
- Persistent state management.
- Beautiful and informative output, powered by Rich.

## Installation

```bash
uv pip install .
```

Or for development:

```bash
uv pip install -e .
```

You can also just use pip directly.

## Usage

### Add a new tunnel

```bash
lpf add <SSH_HOST> <LOCAL_PORT> [-r <REMOTE_PORT>]
```

- `SSH_HOST`: The SSH host (e.g., `user@hostname`).
- `LOCAL_PORT`: The local port to forward from.
- `REMOTE_PORT`: The remote port to forward to (defaults to `LOCAL_PORT`).

Example:

```bash
lpf add my-server.com 8080 -r 80
```

This will forward `localhost:8080` to `my-server.com:80`.

### List tunnels

```bash
lpf ls
```

This will display a table of all configured tunnels and their current status (active/inactive).
