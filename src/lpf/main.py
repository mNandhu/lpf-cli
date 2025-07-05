#!/usr/bin/env python3
import argparse
from . import commands
from .utils import ensure_config_dirs


def main():
    """Main entry point for the lpf-cli command."""
    ensure_config_dirs()

    parser = argparse.ArgumentParser(
        description="A CLI tool to manage local port forwarding tunnels with autossh."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- Add Command ---
    add_parser = subparsers.add_parser("add", help="Add and start a new tunnel")
    add_parser.add_argument("ssh_host", help="The SSH host (e.g., user@hostname)")
    add_parser.add_argument(
        "local_port", type=int, help="The local port to forward from"
    )
    add_parser.add_argument(
        "remote_port",
        type=int,
        nargs="?",
        help="The remote port to forward to (defaults to local_port)",
    )
    add_parser.set_defaults(func=commands.add_tunnel)

    # --- List Command ---
    ls_parser = subparsers.add_parser(
        "ls", help="List all configured tunnels and their status"
    )
    ls_parser.set_defaults(func=commands.list_tunnels)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
