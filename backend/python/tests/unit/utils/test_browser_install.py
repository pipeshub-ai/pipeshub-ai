"""ensure_chromium: fast path, one install across callers, typed failures."""

from __future__ import annotations

import asyncio
import subprocess
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from app.utils import browser_install
from app.utils.browser_install import BrowserInstallError, ensure_chromium

# Each driver pins its own revision, as playwright and patchright releases can.
_DIRS = {"playwright": ["chromium-1"], "patchright": ["chromium-2"]}


@pytest.fixture(autouse=True)
def _manifests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_install, "_required_dirs", _DIRS.get, raising=False)


def _plant(root: Path, module: str, *, complete: bool = True) -> None:
    for name in _DIRS[module]:
        browser = root / name
        browser.mkdir(parents=True, exist_ok=True)
        (browser / "chrome").write_text("browser")
        if complete:
            (browser / "INSTALLATION_COMPLETE").write_text("")


def _plant_chrome(root: Path) -> None:
    for module in _DIRS:
        _plant(root, module)


def test_fast_path_does_not_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    _plant_chrome(tmp_path)
    calls: list[str] = []

    def install(module: str, env: dict[str, str], timeout: float) -> None:
        calls.append(module)

    monkeypatch.setattr(browser_install, "_run_install", install)
    asyncio.run(ensure_chromium())
    assert calls == []


def test_concurrent_callers_install_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    calls: list[str] = []

    def install(module: str, env: dict[str, str], timeout: float) -> None:
        calls.append(module)
        time.sleep(0.15)
        _plant(tmp_path, module)

    monkeypatch.setattr(browser_install, "_run_install", install)

    async def both() -> None:
        await asyncio.gather(ensure_chromium(), ensure_chromium())

    asyncio.run(both())
    assert calls == ["playwright", "patchright"]


def test_failure_raises_typed_error_with_remedies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    def boom(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["playwright", "install", "chromium"])

    monkeypatch.setattr(browser_install.subprocess, "run", boom)
    with pytest.raises(BrowserInstallError, match="full image") as exc_info:
        asyncio.run(ensure_chromium())
    message = str(exc_info.value)
    assert "Playwright CDN" in message
    assert "PLAYWRIGHT_DOWNLOAD_HOST" in message


def test_timeout_raises_typed_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    def boom(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="playwright", timeout=1)

    monkeypatch.setattr(browser_install.subprocess, "run", boom)
    with pytest.raises(BrowserInstallError, match="PLAYWRIGHT_DOWNLOAD_HOST"):
        asyncio.run(ensure_chromium(timeout=0.2))


def test_mirror_host_is_passed_to_the_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setenv("PLAYWRIGHT_DOWNLOAD_HOST", "https://mirror.example/playwright")
    seen: dict[str, str] = {}

    def install(module: str, env: dict[str, str], timeout: float) -> None:
        seen.update(env)
        _plant_chrome(tmp_path)

    monkeypatch.setattr(browser_install, "_run_install", install)
    asyncio.run(ensure_chromium())
    assert seen["PLAYWRIGHT_DOWNLOAD_HOST"] == "https://mirror.example/playwright"
    assert seen["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path)
    assert seen["PATCHRIGHT_BROWSERS_PATH"] == str(tmp_path)


def test_a_driver_whose_install_failed_is_installed_on_the_next_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Playwright's Chromium on disk must not hide that patchright's never arrived."""
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    calls: list[str] = []
    patchright_up = False

    def install(module: str, env: dict[str, str], timeout: float) -> None:
        calls.append(module)
        if module == "patchright" and not patchright_up:
            raise BrowserInstallError("patchright download failed")
        _plant(tmp_path, module)

    monkeypatch.setattr(browser_install, "_run_install", install)
    with pytest.raises(BrowserInstallError):
        asyncio.run(ensure_chromium())

    patchright_up = True
    asyncio.run(ensure_chromium())
    assert calls == ["playwright", "patchright", "patchright"]


def test_an_unfinished_download_does_not_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    for module in _DIRS:
        _plant(tmp_path, module, complete=False)
    calls: list[str] = []

    def install(module: str, env: dict[str, str], timeout: float) -> None:
        calls.append(module)
        _plant(tmp_path, module)

    monkeypatch.setattr(browser_install, "_run_install", install)
    asyncio.run(ensure_chromium())
    assert calls == ["playwright", "patchright"]


def test_the_wait_covers_every_driver_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each install fits its own timeout; together they take longer than one."""
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    def install(module: str, env: dict[str, str], timeout: float) -> None:
        time.sleep(timeout * 0.6)
        _plant(tmp_path, module)

    monkeypatch.setattr(browser_install, "_run_install", install)
    asyncio.run(ensure_chromium(timeout=0.5))
    assert browser_install.chromium_installed()


@pytest.mark.parametrize("module", ["playwright", "patchright"])
def test_required_dirs_come_from_the_driver_manifest(module: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()
    pytest.importorskip(module)
    dirs = browser_install._required_dirs(module)
    assert dirs and any(d.startswith("chromium-") for d in dirs)
    assert all(d.rsplit("-", 1)[1].isdigit() for d in dirs)
