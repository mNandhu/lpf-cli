# Changelog

All notable changes to `lpf` are listed here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Versions come from git tags.

## [Unreleased]

### Added

- `lpf add --name/-n <NAME>` names a tunnel so it can be referred to as
  `<NAME>` in `stop`, `start`, `rm`, and `logs` instead of `SSH_HOST:PORT`.
  Shell completion and `lpf ls`'s new NAME column both cover it.

## [0.3.0] - 2026-09-14

### Added

- `lpf logs <SSH_HOST:PORT>` shows a tunnel's autossh and ssh output, with
  `-n/--lines` and `-f/--follow`.
- `lpf autostart enable|disable|status` installs a systemd user service that
  runs `lpf restart --no-wait` at login (or at boot with lingering). It warns
  when the service would use a different ssh-agent than your shell.
- `lpf add` takes several ports at once: `lpf add myserver 8000 8001 8002`.
- `lpf add <TAB>` completes hosts from `~/.ssh/config`, following `Include`.
- `--no-wait` for `start` and `restart`.
- `lpf ls` has a `CONNECTING` status for tunnels whose autossh is running but
  whose ssh hasn't connected.
- `LPF_CONFIG_DIR` sets where `lpf` keeps its state.
- A pytest test suite.

### Changed

- `add`, `start`, and `restart` wait up to 15 seconds for each tunnel to
  connect and exit with status 1 if one doesn't.
- ssh runs with `BatchMode=yes`, so a password or passphrase prompt fails
  (and shows up in `lpf logs`) instead of hanging unseen. Keys need to work
  without a prompt.
- ssh runs with `ExitOnForwardFailure=yes`, so a forward that can't bind its
  port is retried rather than left half working.

### Fixed

- `add` reported "started successfully" for tunnels that never connected,
  such as an unknown host. It now shows ssh's error.
- `ls` showed `ACTIVE` for tunnels that weren't connected.
- `start --all` stopped at the first tunnel that failed.
- A missing `autossh` crashed with a traceback. It now says how to install it.
- A tunnel could start, and be reported as connected, while another process
  held its local port.
- The port check only worked on Linux and only looked at IPv4.
- On macOS, a reused PID could make a dead tunnel look alive.
- `restart --force` could start a tunnel before the old one released its port.
- A crash while saving could corrupt `tunnels.json`. Saves are now atomic.

## [0.2.1] - 2026-09-14

### Added

- Once a day, `lpf` checks GitHub for a newer release in a background process
  and prints a notice after the command's output. Set
  `LPF_NO_UPDATE_CHECK=1` to turn it off.

## [0.2.0] - 2026-09-14

First tagged release.

### Added

- `lpf --version`.
- Versions are derived from git tags with hatch-vcs.
- Installation with `uv tool install`, documented in a rewritten README.
- `-H/--remote-host` forwards to a host the SSH server can reach, like a
  container IP.
- `stop`, `start`, and `rm` accept `SSH_HOST PORT` as two arguments as well as
  `SSH_HOST:PORT`, with port completion.
- `stop` and `start` commands.
- Shell completion for commands and tunnel IDs.
- `restart` (with `--force`), `sync`, `rm --all`, and `add --force`.

### Fixed

- `add` missed conflicts with stopped tunnels on the same local port.

[Unreleased]: https://github.com/mNandhu/lpf-cli/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/mNandhu/lpf-cli/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/mNandhu/lpf-cli/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/mNandhu/lpf-cli/releases/tag/v0.2.0
