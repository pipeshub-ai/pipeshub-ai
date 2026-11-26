"""Links into a fetched repository at the fetched commit (ADR-RUN-01 D3: repository files are first-class sources).

A run that fetched ``https://github.com/onyx-dot-app/onyx`` at commit ``4f83…`` may cite
``https://github.com/onyx-dot-app/onyx/blob/4f83…/backend/onyx/main.py``: the file as it was at the commit the run
read. ``repository_file`` reads a link back to its ``RepositoryRef`` only for a path that exists in the fetched tree
at that commit (``RepositoryTrees``, the fetch's own ``git ls-tree``); a link at a branch is rewritten to the fetched
commit when its path exists there (the content the run read), and any other link is no source. ``blob_url`` builds
the page a citation opens: a file's blob view, a folder's tree view, a commit's page. The host forms are GitHub's and
Gitea's (``/blob/``, ``/tree/``), GitLab's (``/-/blob/``, ``/-/tree/``) and Bitbucket's (``/src/``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol
from urllib.parse import unquote, urlsplit

from harness_core.provenance.types import RepositoryRef

if TYPE_CHECKING:
    from collections.abc import Iterable

_PAGE: Final = re.compile(r"^/(?P<repo>.+?)(?:/-)?/(?:blob|tree|src|raw)/(?P<rest>.+?)/?$")
_COMMIT_PAGE: Final = re.compile(r"^/(?P<repo>.+?)(?:/-)?/commits?/(?P<commit>[0-9a-f]{7,64})/?$")
_HEX: Final = re.compile(r"[0-9a-f]{7,64}")
# The shortest commit prefix a link may abbreviate the fetched commit to (git's own default abbreviation).
MIN_COMMIT_PREFIX: Final = 7


@dataclass(frozen=True, slots=True)
class RepositoryTree:
    """The files and folders at a fetched commit (``git ls-tree -r -t``); the root is a folder."""

    files: frozenset[str]
    folders: frozenset[str]

    def kind(self, path: str) -> Literal["file", "folder"] | None:
        if not path or path in self.folders:
            return "folder"
        return "file" if path in self.files else None


class RepositoryTrees(Protocol):
    def tree(self, source: RepositoryRef) -> RepositoryTree | None:
        """The tree at ``source``'s commit; ``None`` when it cannot be read (nothing in it is then citable)."""
        ...


def _repository(url: str) -> tuple[str, str] | None:
    """``(host, owner/name)`` of a repository's address, without ``.git``."""
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        return None
    path = parts.path.strip("/").removesuffix(".git")
    return (parts.hostname.lower(), path) if path else None


def repository_label(source: RepositoryRef) -> str:
    """``host/owner/name``: how the fetch's cache names the repository."""
    found = _repository(source.url)
    return f"{found[0]}/{found[1]}" if found is not None else source.url


def blob_url(source: RepositoryRef) -> str:
    """The host's page for ``source`` at its commit: the file's, the folder's tree view, or the commit's."""
    found = _repository(source.url)
    host, repo = found if found is not None else ("", source.url)
    base = f"https://{host}/{repo}"
    if not source.path and not source.folder:
        return f"{base}/commit/{source.commit}"
    gitlab, bitbucket = "gitlab" in host, "bitbucket" in host
    if bitbucket:
        marker = "/src/"
    elif source.folder:
        marker = "/-/tree/" if gitlab else "/tree/"
    else:
        marker = "/-/blob/" if gitlab else "/blob/"
    return f"{base}{marker}{source.commit}/{source.path}".rstrip("/")


def label(source: RepositoryRef) -> str:
    """What a person reads for the source: ``owner/name · path`` (the repository alone when it names no file)."""
    found = _repository(source.url)
    repo = found[1] if found is not None else source.url
    return f"{repo} · {source.path}" if source.path else repo


def _checked(repository: RepositoryRef, path: str, trees: RepositoryTrees) -> RepositoryRef | None:
    tree = trees.tree(repository)
    kind = tree.kind(path) if tree is not None else None
    return repository.at(path, folder=kind == "folder") if kind is not None else None


def repository_file(link: str, fetched: Iterable[RepositoryRef], trees: RepositoryTrees) -> RepositoryRef | None:
    """The file or folder ``link`` names in one of the ``fetched`` repositories, at its fetched commit, when that path
    exists in the fetched tree; ``None`` otherwise. A link at the fetched commit (or a 7+ character prefix of it) is
    read at it; a link at a branch or tag is rewritten to the fetched commit when its path exists there (a branch
    with slashes in its name is tried at each split); a commit page names the repository itself."""
    parts = urlsplit(link.strip().rstrip(".,;:"))
    host = (parts.hostname or "").lower()
    path = unquote(parts.path)
    if not host:
        return None
    commit_page = _COMMIT_PAGE.match(path)
    page = None if commit_page is not None else _PAGE.match(path)
    if commit_page is None and page is None:
        return None
    repo = (commit_page or page).group("repo").removesuffix(".git")  # type: ignore[union-attr]
    for source in fetched:
        if _repository(source.url) != (host, repo):
            continue
        if commit_page is not None:
            commit = commit_page.group("commit")
            return RepositoryRef(source.url, source.commit) if source.commit.startswith(commit) else None
        assert page is not None
        segments = page.group("rest").split("/")
        head, rest = segments[0], "/".join(segments[1:])
        if _HEX.fullmatch(head) and len(head) >= MIN_COMMIT_PREFIX:
            # A commit: only the fetched one is the content the run read.
            return _checked(source, rest, trees) if source.commit.startswith(head) else None
        if len(segments) == 1:
            # ``/tree/<branch>``: the repository's root at the fetched commit.
            return _checked(source, "", trees)
        for split in range(1, len(segments)):
            found = _checked(source, "/".join(segments[split:]), trees)
            if found is not None:
                return found
        return None
    return None


__all__ = [
    "MIN_COMMIT_PREFIX",
    "RepositoryTree",
    "RepositoryTrees",
    "blob_url",
    "label",
    "repository_file",
    "repository_label",
]
