from __future__ import annotations

import base64
from typing import Any

from app.agent_loop_lib.sandbox.browser_sandbox import (
    BrowserSandboxError,
    PlaywrightBrowserSandbox,
)
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)

"""The browser sandbox's toolset — every tool shares one
`PlaywrightBrowserSandbox` instance (one page, navigated/mutated across
calls, same shape as the filesystem tools sharing one WorkspaceBackend)."""


class BrowserNavigateTool(Tool):
    def __init__(self, sandbox: PlaywrightBrowserSandbox) -> None:
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        return "browser_navigate"

    @property
    def short_description(self) -> str:
        return "Navigate the sandboxed browser to a URL."

    @property
    def description(self) -> str:
        return "Navigate the sandboxed browser to a URL. Returns the final URL after any redirects."

    @property
    def path(self) -> str:
        return "/toolsets/browser/browser_navigate"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("risk", "medium")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="url", type=ParameterType.STRING, required=True, description="URL to navigate to.")]

    async def execute(self, url: str, **kwargs: Any) -> ToolOutput:
        try:
            final_url = await self._sandbox.navigate(url)
        except BrowserSandboxError as e:
            return ToolOutput(success=True, data={"error": str(e)})
        return ToolOutput(success=True, data={"url": final_url})


class BrowserGetTextTool(Tool):
    def __init__(self, sandbox: PlaywrightBrowserSandbox) -> None:
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        return "browser_get_text"

    @property
    def short_description(self) -> str:
        return "Get the visible text content of the current page."

    @property
    def description(self) -> str:
        return "Get the visible text content of the current page."

    @property
    def path(self) -> str:
        return "/toolsets/browser/browser_get_text"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> ToolOutput:
        try:
            text = await self._sandbox.get_text()
        except BrowserSandboxError as e:
            return ToolOutput(success=True, data={"error": str(e)})
        return ToolOutput(success=True, data={"text": text})


class BrowserClickTool(Tool):
    def __init__(self, sandbox: PlaywrightBrowserSandbox) -> None:
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        return "browser_click"

    @property
    def short_description(self) -> str:
        return "Click an element on the current page."

    @property
    def description(self) -> str:
        return "Click an element on the current page identified by a CSS selector."

    @property
    def path(self) -> str:
        return "/toolsets/browser/browser_click"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("risk", "medium")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="selector", type=ParameterType.STRING, required=True, description="CSS selector of the element to click.")]

    async def execute(self, selector: str, **kwargs: Any) -> ToolOutput:
        try:
            await self._sandbox.click(selector)
        except BrowserSandboxError as e:
            return ToolOutput(success=True, data={"error": str(e)})
        return ToolOutput(success=True, data={"status": "ok"})


class BrowserFillTool(Tool):
    def __init__(self, sandbox: PlaywrightBrowserSandbox) -> None:
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        return "browser_fill"

    @property
    def short_description(self) -> str:
        return "Fill a form field on the current page."

    @property
    def description(self) -> str:
        return "Fill a form field on the current page identified by a CSS selector."

    @property
    def path(self) -> str:
        return "/toolsets/browser/browser_fill"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("risk", "medium")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="selector", type=ParameterType.STRING, required=True, description="CSS selector of the field to fill."),
            ToolParameter(name="text", type=ParameterType.STRING, required=True, description="Text to type into the field."),
        ]

    async def execute(self, selector: str, text: str, **kwargs: Any) -> ToolOutput:
        try:
            await self._sandbox.fill(selector, text)
        except BrowserSandboxError as e:
            return ToolOutput(success=True, data={"error": str(e)})
        return ToolOutput(success=True, data={"status": "ok"})


class BrowserScreenshotTool(Tool):
    def __init__(self, sandbox: PlaywrightBrowserSandbox) -> None:
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        return "browser_screenshot"

    @property
    def short_description(self) -> str:
        return "Take a screenshot of the current page."

    @property
    def description(self) -> str:
        return "Take a screenshot of the current page. Returns base64-encoded PNG data."

    @property
    def path(self) -> str:
        return "/toolsets/browser/browser_screenshot"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> ToolOutput:
        try:
            png_bytes = await self._sandbox.screenshot()
        except BrowserSandboxError as e:
            return ToolOutput(success=True, data={"error": str(e)})
        return ToolOutput(
            success=True,
            data={"image_base64": base64.b64encode(png_bytes).decode("ascii"), "format": "png"},
        )
