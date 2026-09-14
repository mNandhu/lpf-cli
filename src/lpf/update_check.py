"""
Once-a-day check for a newer lpf release on GitHub.

Commands never wait on the network: `maybe_notify` only reads a cached result,
and when that cache is older than a day it starts a detached process
(`python -m lpf.update_check`) to refresh it for the next run.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from importlib.metadata import version

from rich.console import Console

from .config import CONFIG_DIR
from .utils import write_json_atomic

CACHE_FILE = CONFIG_DIR / "update-check.json"
TAGS_URL = "https://api.github.com/repos/mNandhu/lpf-cli/tags?per_page=100"
CHECK_INTERVAL = 24 * 60 * 60
FETCH_TIMEOUT = 5

stderr_console = Console(stderr=True)


def _parse_version(text: str) -> tuple[int, int, int, int] | None:
    """Parse a version or tag, e.g. 'v0.2.0' or '0.2.1.dev1+g1eb6778ec', into a sortable tuple.

    The last element is 1 for a release and 0 for anything else (like hatch-vcs
    dev builds), so '0.2.1.dev1' sorts below the '0.2.1' release it leads up to.
    A '+local' suffix alone (e.g. '+d20260914' for uncommitted changes) still
    counts as a release.
    """
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return None
    major, minor, patch = match.groups()
    rest = text[match.end() :]
    is_release = 1 if rest == "" or rest.startswith("+") else 0
    return int(major), int(minor), int(patch), is_release


def _read_cache() -> dict:
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_cache(cache: dict):
    write_json_atomic(CACHE_FILE, cache)


def _fetch_latest_tag() -> str | None:
    """Return the highest release tag (vN.N.N) on GitHub. The tags API isn't sorted by version."""
    request = urllib.request.Request(
        TAGS_URL, headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
        tags = json.load(response)
    releases = [
        v for tag in tags if (v := _parse_version(tag["name"])) and v[3] == 1
    ]
    if not releases:
        return None
    return ".".join(str(part) for part in max(releases)[:3])


def refresh_cache():
    """Fetch the latest release and store it. Runs in the detached child process."""
    latest = _fetch_latest_tag()
    if latest:
        _write_cache({"checked_at": time.time(), "latest": latest})


def _start_background_refresh(cache: dict):
    # Record the attempt up front, so being offline doesn't start a new
    # refresh on every command.
    _write_cache({**cache, "checked_at": time.time()})
    subprocess.Popen(
        [sys.executable, "-m", "lpf.update_check"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def maybe_notify():
    """Print a notice if a newer release is cached, and refresh a stale cache in the background."""
    try:
        if os.environ.get("LPF_NO_UPDATE_CHECK"):
            return
        # Shell completion runs lpf on every TAB; any output would corrupt it.
        if any(key.startswith("_LPF_COMPLETE") for key in os.environ):
            return
        if not sys.stderr.isatty():
            return
        if "--help" in sys.argv[1:]:
            return

        installed_text = version("lpf-cli")
        installed = _parse_version(installed_text)
        # 0.0.0 is the fallback for builds without git metadata. It would
        # compare older than every release and nag forever.
        if installed is None or installed[:3] == (0, 0, 0):
            return

        cache = _read_cache()
        latest = _parse_version(cache.get("latest", ""))
        if latest and latest > installed:
            stderr_console.print(
                f"\n[yellow]lpf {cache['latest']} is available "
                f"(you have {installed_text}).[/yellow] "
                "Upgrade with: [bold]uv tool upgrade lpf-cli[/bold]",
                highlight=False,
            )

        if time.time() - cache.get("checked_at", 0) > CHECK_INTERVAL:
            _start_background_refresh(cache)
    except Exception:
        # An update check must never break or change the outcome of a command.
        pass


if __name__ == "__main__":
    try:
        refresh_cache()
    except Exception:
        pass
