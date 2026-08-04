from __future__ import annotations

from app.agent_loop_lib.core.exceptions import AgentLoopError

"""Browser sandbox (Phase 3 taxonomy) — a playwright-backed web toolset;
doubles as a Cursor-style self-verification surface for web work (an agent
can navigate to and inspect a page it just built/deployed). `playwright` is
an optional dependency (`pip install agent-loop[browser]`, then
`playwright install chromium`) — importing it lazily here means the rest of
agent-loop works with zero footprint when the browser sandbox isn't used.
"""


class BrowserSandboxError(AgentLoopError):
    """Browser sandbox unavailable, or a browser action failed."""


class PlaywrightBrowserSandbox:
    """Launches a single headless (by default) Chromium browser + page on
    first use, shared across every tool call until `close()`. Local backend
    only for now, matching the roadmap's "local backends first" sequencing
    for the sandbox taxonomy — a remote (browserless/E2B) backend can
    implement the same duck-typed interface later."""

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._playwright = None
        self._browser = None
        self._page = None

    async def _ensure_page(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise BrowserSandboxError(
                "playwright is not installed — install it with `pip install agent-loop[browser]` "
                "and run `playwright install chromium` to use the browser sandbox."
            ) from e
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        self._page = await self._browser.new_page()
        return self._page

    async def navigate(self, url: str) -> str:
        page = await self._ensure_page()
        await page.goto(url)
        return page.url

    async def get_text(self) -> str:
        page = await self._ensure_page()
        return await page.inner_text("body")

    async def click(self, selector: str) -> None:
        page = await self._ensure_page()
        await page.click(selector)

    async def fill(self, selector: str, text: str) -> None:
        page = await self._ensure_page()
        await page.fill(selector, text)

    async def screenshot(self) -> bytes:
        page = await self._ensure_page()
        return await page.screenshot()

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._browser = None
        self._page = None
        self._playwright = None

    async def __aenter__(self) -> "PlaywrightBrowserSandbox":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
