"""Download Chromium the first time the slim image needs a browser.

The full image already contains Chromium, so this returns without a download.
Slim stores the browsers on the data volume (``PLAYWRIGHT_BROWSERS_PATH``) so
they survive restarts. The web connector and the browser sandbox both call
this; a file lock keeps those processes from downloading at the same time.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

_BROWSER_FILE_NAMES = frozenset(
    {"chrome", "chromium", "headless_shell", "chrome-headless-shell"}
)
_INSTALL_MODULES = ("playwright", "patchright")
_DEFAULT_TIMEOUT_SECONDS = 600.0

_FAILURE = (
    "Chromium is not installed and the download failed. Use the full image, "
    "which already contains Chromium; allow outbound access to the Playwright CDN; "
    "or set PLAYWRIGHT_DOWNLOAD_HOST to a mirror."
)


class BrowserInstallError(RuntimeError):
    """Chromium is not installed and could not be downloaded."""


def browsers_dir() -> Path:
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.environ.get(
        "PATCHRIGHT_BROWSERS_PATH"
    )
    if configured:
        return Path(configured)
    return Path.home() / ".cache" / "ms-playwright"


def chromium_installed() -> bool:
    root = browsers_dir()
    if not root.is_dir():
        return False
    for _dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name in _BROWSER_FILE_NAMES or name.startswith("chrome"):
                return True
    return False


def _run_install(module: str, env: dict[str, str], timeout: float) -> None:
    try:
        subprocess.run(
            [sys.executable, "-m", module, "install", "chromium"],
            env=env,
            check=True,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise BrowserInstallError(_FAILURE) from exc


def _install_under_lock(timeout: float) -> None:
    import fcntl

    dest = browsers_dir()
    dest.mkdir(parents=True, exist_ok=True)
    lock_path = dest / ".install.lock"
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            if chromium_installed():
                return
            env = os.environ.copy()
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(dest)
            env["PATCHRIGHT_BROWSERS_PATH"] = str(dest)
            for module in _INSTALL_MODULES:
                _run_install(module, env, timeout)
            if not chromium_installed():
                raise BrowserInstallError(_FAILURE)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


async def ensure_chromium(*, timeout: float | None = None) -> None:
    """Install Playwright and Patchright Chromium if they are not already present."""
    if chromium_installed():
        return
    if timeout is None:
        timeout = float(os.environ.get("PIPESHUB_BROWSER_INSTALL_TIMEOUT", _DEFAULT_TIMEOUT_SECONDS))
    try:
        await asyncio.wait_for(asyncio.to_thread(_install_under_lock, timeout), timeout=timeout)
    except BrowserInstallError:
        raise
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise BrowserInstallError(_FAILURE) from exc
