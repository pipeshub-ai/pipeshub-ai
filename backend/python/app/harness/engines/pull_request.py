"""Opening a run's pull request from the control plane (GIT-FLOW, GT-04).

An engine run that works on a repository pushes only its session branch (the egress proxy's git rule). Opening the
pull request is not the engine's: it calls ``open_pull_request`` with a title, a summary and the citations it
relies on, and the Tool Gateway runs it here, on the worker. It is approved exactly as the toolset's
``create_pull_request`` is (main #4313: a toolset write runs unless a rule asks; then the run's approval hold
asks the person first). The control plane, not the model, names the repository, the head (the session branch) and the base, resolves the citations to their links,
and adds the session link with the write identity's disclosure (OWD-18). The pull request itself is created by
the run's GitHub toolset (``create_pull_request``), with the person's own connection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast, override
from urllib.parse import urlsplit

from app.harness.write_identity.disclosure import disclosed
from app.harness.write_identity.targets import DISCLOSED
from harness_core.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from app.harness.write_identity.disclosure import WriteIdentitySettings
    from harness_core.logs import HarnessLog
    from harness_core.tools.registry import ToolRegistry

OPEN_PULL_REQUEST = "open_pull_request"
OPEN_PULL_REQUEST_PATH = "/tools/git/open_pull_request"
GITHUB_CREATE_PULL_REQUEST = "/github/create_pull_request"
# The disclosure a pull request carries is the GitHub toolset's own (its body, in markdown).
_PULL_REQUEST_DISCLOSURE = DISCLOSED[("github", "create_pull_request")]
MAX_CITATIONS = 50
_REF = re.compile(r"ref\d{1,6}")


@dataclass(frozen=True, slots=True)
class PullRequestTarget:
    """Where the run's pull request goes: the repository (``owner/name`` on GitHub), the session branch, the base."""

    owner: str
    repo: str
    head: str
    base: str


def github_target(url: str, head: str, base: str) -> PullRequestTarget | None:
    """The pull request target of a GitHub repository URL; ``None`` for any other host or shape."""
    parts = urlsplit(url)
    path = parts.path.strip("/").removesuffix(".git").split("/")
    if parts.hostname not in ("github.com", "www.github.com") or len(path) != 2 or not all(path):
        return None
    return PullRequestTarget(path[0], path[1], head, base)


def pull_request_body(summary: str, citations: Sequence[tuple[str, str]]) -> str:
    """The summary and a Sources section of the citations' links, in the order the engine named them."""
    text = summary.strip()
    if not citations:
        return text
    sources = "\n".join(f"{index}. <{url}>" for index, (_, url) in enumerate(citations, 1))
    return f"{text}\n\n### Sources\n{sources}"


def cited(refs: Sequence[str], ref_to_url: Mapping[str, str]) -> list[tuple[str, str]]:
    """The citations the engine named that this run's tools returned, each once; any other ref is dropped."""
    seen: list[tuple[str, str]] = []
    for ref in refs:
        url = ref_to_url.get(ref) if _REF.fullmatch(ref) else None
        if url is not None and all(r != ref for r, _ in seen):
            seen.append((ref, url))
    return seen[:MAX_CITATIONS]


class OpenPullRequestTool(Tool):
    """``create`` (the run's GitHub ``create_pull_request``) for the run's own repository and session branch."""

    def __init__(
        self,
        create: Tool,
        target: PullRequestTarget,
        ref_to_url: Callable[[], Mapping[str, str]],
        identity: WriteIdentitySettings | None,
        log: HarnessLog,
    ) -> None:
        self._create = create
        self._target = target
        self._ref_to_url = ref_to_url
        self._identity = identity
        self._log = log

    @property
    @override
    def name(self) -> str:
        return OPEN_PULL_REQUEST

    @property
    @override
    def short_description(self) -> str:
        return "Open a pull request for this session's branch"

    @property
    @override
    def description(self) -> str:
        t = self._target
        return (
            f"Opens a pull request in {t.owner}/{t.repo} from this session's branch ({t.head}) into {t.base}, after "
            "the person approves it. Push your commits to that branch first (ph git push, which asks the person). "
            "Give a short "
            "title, a summary of the change for reviewers, and the citation refs (e.g. ref3) of the sources the "
            "change relies on; the link to this session is added for you."
        )

    @property
    @override
    def path(self) -> str:
        return OPEN_PULL_REQUEST_PATH

    @property
    @override
    def display_name(self) -> str | None:
        return "Open pull request"

    @property
    @override
    def tags(self) -> list[Tag]:
        return self._create.tags

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="title", type=ParameterType.STRING, description="Pull request title", required=True),
            ToolParameter(
                name="summary", type=ParameterType.STRING, description="What changed and why, for reviewers",
                required=True,
            ),
            ToolParameter(
                name="citations",
                type=ParameterType.ARRAY,
                description="Citation refs (ref1, ref2, ...) of the sources the change relies on",
                required=False,
                items={"type": "string"},
            ),
            ToolParameter(
                name="draft", type=ParameterType.BOOLEAN, description="Open it as a draft", required=False
            ),
        ]

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        title = str(kwargs.get("title") or "").strip()
        summary = str(kwargs.get("summary") or "").strip()
        if not title or not summary:
            return ToolOutput(success=False, error="A pull request needs a title and a summary.")
        raw: object = kwargs.get("citations") or []
        refs = [str(r) for r in cast("list[object]", raw)] if isinstance(raw, list) else [str(raw)]
        citations = cited(refs, self._ref_to_url())
        arguments: dict[str, object] = {
            "owner": self._target.owner,
            "repo": self._target.repo,
            "title": title,
            "head": self._target.head,
            "base": self._target.base,
            "body": pull_request_body(summary, citations),
            "draft": kwargs.get("draft") is True,
        }
        if self._identity is not None:
            refused = self._identity.refusal(_PULL_REQUEST_DISCLOSURE.action, OPEN_PULL_REQUEST_PATH, "pull requests")
            if refused is not None:
                return ToolOutput(success=False, error=refused)
            _ = disclosed(self._identity, _PULL_REQUEST_DISCLOSURE, arguments)
        self._log.info(
            "engine.git.pull_request", citations=len(citations), dropped=len(refs) - len(citations),
            disclosed=self._identity is not None,
        )
        return await self._create.execute(**arguments)


def github_create_tool(registry: ToolRegistry) -> Tool | None:
    """The run's GitHub ``create_pull_request``, when its grant holds the GitHub toolset."""
    for name in registry.names():
        path = registry.path_for_name(name) or ""
        if path.endswith(GITHUB_CREATE_PULL_REQUEST):
            return registry.resolve_by_name(name)
    return None


__all__ = [
    "OPEN_PULL_REQUEST",
    "OPEN_PULL_REQUEST_PATH",
    "OpenPullRequestTool",
    "PullRequestTarget",
    "cited",
    "github_create_tool",
    "github_target",
    "pull_request_body",
]
