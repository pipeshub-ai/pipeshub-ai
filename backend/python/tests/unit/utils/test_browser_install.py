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


def _plant_chrome(root: Path) -> None:
    chrome = root / "chromium-test" / "chrome"
    chrome.parent.mkdir(parents=True, exist_ok=True)
    chrome.write_text("browser")


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
        _plant_chrome(tmp_path)

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
