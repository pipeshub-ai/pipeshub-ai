"""Links into a fetched repository, cited as sources (ADR-RUN-01 D3, WP-83 open item 2).

An engine that fetched a repository writes links into it: ``[main.py](https://github.com/onyx-dot-app/onyx/blob/
4f83…/backend/onyx/main.py)``. A link into a repository the session's ``ProvenanceLedger`` holds as fetched
publicly, whose path exists in the tree at the fetched commit, is a source the run read; a link at a branch is
rewritten to the fetched commit's permalink when its path exists there (one research run wrote 38 such links). The source
is put into the run's citation state as a web record of kind ``repository`` (the host's page for the file or folder
at the commit, titled ``onyx-dot-app/onyx · backend/onyx/main.py``) and the link becomes a ``[label](refN)``: the
answer pipeline, a parent adopting a child's sources (ENGCITE2) and a resumed attempt then cite it as they cite a web
page. A link to a path the fetched tree does not hold, or into a repository the run did not fetch, stays a plain
link. Only a public fetch is cited: a citation of kind web is shown to every viewer of the conversation.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

from harness_core.provenance.repository import blob_url, label, repository_file

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.provenance.repository import RepositoryTree
    from harness_core.provenance.types import RepositoryRef

REPOSITORY_SOURCE_KIND: Final = "repository"
_LINK: Final = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<target>https?://[^)\s]+)\)")
_BARE: Final = re.compile(r"(?<![(\[<])\bhttps?://[^\s<>\]\)\"'`]+")


def _register(state: dict[str, Any], source: RepositoryRef) -> str:
    """The ref of ``source``'s page in the run's citation state, adding its web record the first time."""
    from app.utils.chat_helpers import CitationRefMapper

    mapper = state.get("citation_ref_mapper")
    if mapper is None:
        mapper = state["citation_ref_mapper"] = CitationRefMapper()
    url = blob_url(source)
    found = state.get("web_records")
    records: list[Any] = cast("list[Any]", found) if isinstance(found, list) else []
    state["web_records"] = records
    if not any(isinstance(r, dict) and cast("dict[str, Any]", r).get("url") == url for r in records):
        records.append({
            "url": url,
            "title": label(source),
            "content": f"{label(source)} at commit {source.commit[:12]}",
            "sourceKind": REPOSITORY_SOURCE_KIND,
        })
    return str(mapper.get_or_create_ref(url))


class CitableRepositories(Protocol):
    """The repositories a run may cite (fetched publicly, from the session's provenance ledger) and their trees."""

    def fetched(self) -> Sequence[RepositoryRef]: ...

    def tree(self, source: RepositoryRef) -> RepositoryTree | None: ...


class _NoRepositories:
    def fetched(self) -> Sequence[RepositoryRef]:
        return ()

    def tree(self, source: RepositoryRef) -> RepositoryTree | None:
        del source


NO_REPOSITORIES: Final[CitableRepositories] = _NoRepositories()


def cite_repository_links(
    text: str,
    context: Any,  # noqa: ANN401 - AgentContext
    repositories: CitableRepositories,
) -> str:
    """``text`` with every link into a ``fetched`` repository whose path exists in its fetched tree as
    ``[label](refN)`` of that path's page at the fetched commit, registered in ``context``'s citation state; a link at a
    branch becomes the fetched commit's permalink; every other link as it was."""
    fetched = repositories.fetched()
    if not text or not fetched:
        return text
    state: dict[str, Any] = context.tool_state

    def linked(match: re.Match[str]) -> str:
        found = repository_file(match.group("target"), fetched, repositories)
        if found is None:
            return match.group(0)
        shown = match.group("label").strip() or label(found)
        return f"[{shown}]({_register(state, found)})"

    def bare(match: re.Match[str]) -> str:
        raw = match.group(0)
        link = raw.rstrip(".,;:")
        found = repository_file(link, fetched, repositories)
        if found is None:
            return raw
        return f"[{label(found)}]({_register(state, found)}){raw[len(link):]}"

    return _BARE.sub(bare, _LINK.sub(linked, text))


__all__ = ["NO_REPOSITORIES", "REPOSITORY_SOURCE_KIND", "CitableRepositories", "cite_repository_links"]
