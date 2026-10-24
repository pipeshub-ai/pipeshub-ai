"""Download Chromium the first time the slim image needs a browser.

The full image already contains Chromium, so this returns without a download.
Slim stores the browsers on the data volume (``PLAYWRIGHT_BROWSERS_PATH``) so
they survive restarts. The web connector and the browser sandbox both call
this; a file lock keeps those processes from downloading at the same time.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

# What `<driver> install chromium` puts under the browsers path, by manifest name.
_CHROMIUM_BROWSERS = frozenset({"chromium", "chromium-headless-shell"})
# Playwright writes this into a browser's directory once its download is complete.
_INSTALL_COMPLETE = "INSTALLATION_COMPLETE"
_INSTALL_MODULES = ("playwright", "patchright")
# Per driver: one Chromium download (about 150 MB) on a slow link.
_INSTALL_TIMEOUT_SECONDS = 600.0

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


def _required_dirs(module: str) -> list[str] | None:
    """The browser directories ``module install chromium`` creates, from the driver's own
    manifest; None when the driver or its manifest cannot be found."""
    spec = importlib.util.find_spec(module)
    if spec is None or spec.origin is None:
        return None
    manifest = Path(spec.origin).parent / "driver" / "package" / "browsers.json"
    try:
        browsers = json.loads(manifest.read_text(encoding="utf-8"))["browsers"]
        return [
            f"{b['name'].replace('-', '_')}-{b['revision']}"
            for b in browsers
            if b["name"] in _CHROMIUM_BROWSERS
        ] or None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def driver_installed(module: str) -> bool:
    """Each driver pins its own Chromium revision, so one driver's download does not
    stand in for the other's, and a half-finished download does not count."""
    dirs = _required_dirs(module)
    if not dirs:
        return False
    root = browsers_dir()
    return all((root / name / _INSTALL_COMPLETE).is_file() for name in dirs)


def chromium_installed() -> bool:
    return all(driver_installed(module) for module in _INSTALL_MODULES)


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
                if not driver_installed(module):
                    _run_install(module, env, timeout)
            if not chromium_installed():
                raise BrowserInstallError(_FAILURE)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


async def ensure_chromium(*, timeout: float = _INSTALL_TIMEOUT_SECONDS) -> None:
    """Install Playwright and Patchright Chromium if they are not already present."""
    if chromium_installed():
        return
    try:
        # ``timeout`` bounds each driver's install; the wait has to cover all of them,
        # or a slow but successful install is reported as a failure.
        await asyncio.wait_for(
            asyncio.to_thread(_install_under_lock, timeout),
            timeout=timeout * len(_INSTALL_MODULES),
        )
    except BrowserInstallError:
        raise
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise BrowserInstallError(_FAILURE) from exc
