"""Records that point at other records by web address, across connectors.

A Slack message that pastes a pull request's address, or a ticket that quotes a
wiki page's, names a record another connector stores. These helpers turn such an
address into the forms that record's ``webUrl`` can take, so the two records can
be joined by an exact, indexed lookup.

Only the address is compared, never a guess from the text around it: a join is
made when one record's ``webUrl`` is the address another record links to.

Named-entity extraction keeps every address a record mentions as a ``url``
entity (``app.modules.named_entities``). This is the other half: it finds the
record that lives at the address. The generic form is built by the same
normaliser, so the two agree on what counts as one address.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

from app.config.constants.arangodb import RecordRelations
from app.models.entities import RecordType, RelatedExternalRecord
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.normalizers.contacts import normalize_contact

# Lookups per record are bounded: a message pasting a long list of addresses
# costs at most this many index reads when it is saved.
MAX_LINKED_URLS = 25

# Every form a record's address can be stored under is looked up exactly, so
# this caps the reads per address as well.
_MAX_CANDIDATES = 8

# ``/{owner}/{repo}/pull/{n}`` or ``/issues/{n}``, then any tab or anchor.
_GITHUB_PATH = re.compile(r"^/([^/]+)/([^/]+)/(pull|issues)/(\d+)(?:/.*)?$")
# The tabs of a pull request page. A pasted link often opens one of them.
_GITHUB_PR_TABS = ("files", "commits", "checks", "changes")
# ``.../browse/KEY-123``. Jira serves an issue at this address on every site.
_JIRA_PATH = re.compile(r"^(.*?)/browse/([A-Z][A-Z0-9_]+-\d+)/?$")
# ``.../wiki/spaces/SPACE/pages/123/Title``. The title part changes with the page.
_CONFLUENCE_PATH = re.compile(r"^(.*?/spaces/[^/]+/pages/\d+)(?:/[^/]*)?/?$")

# Characters that end a sentence more often than an address.
_TRAILING = ".,;:!?)]}>'\""


@dataclass(frozen=True)
class LinkTarget:
    """Where a linked address can be found.

    ``candidates`` are the exact ``webUrl`` values the linked record may be
    stored under, most likely first. ``record_type`` is the kind of record the
    address usually names; it is only a label, since a link never creates one.
    """

    url: str
    candidates: tuple[str, ...]
    record_type: RecordType


def _clean(url: str) -> str:
    url = url.strip().replace("&amp;", "&")
    while url and url[-1] in _TRAILING:
        url = url[:-1]
    return url


def _dedup(values: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return tuple(seen[:_MAX_CANDIDATES])


def _origin(parts: SplitResult) -> str:
    # userinfo (``user:pass@``) is a credential, never part of the address.
    host = parts.netloc.rsplit("@", 1)[-1].casefold()
    return f"{(parts.scheme or 'https').casefold()}://{host}"


def _without_fragment(parts: SplitResult) -> str:
    return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, parts.query, ""))


def link_target(url: str) -> LinkTarget | None:
    """The forms the record at ``url`` may be stored under, or None when ``url``
    is not a web address."""
    if not isinstance(url, str):
        return None
    raw = _clean(url)
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme.casefold() not in ("http", "https") or not parts.netloc:
        return None
    origin = _origin(parts)
    path = parts.path

    github = _GITHUB_PATH.match(path)
    if github:
        owner, repo, kind, number = github.groups()
        base = f"{origin}/{owner}/{repo}"
        pull, issue = f"{base}/pull/{number}", f"{base}/issues/{number}"
        # GitHub serves a pull request at either address; the record keeps the
        # one GitHub gave it.
        if kind == "pull":
            return LinkTarget(pull, _dedup([pull, issue]), RecordType.PULL_REQUEST)
        return LinkTarget(issue, _dedup([issue, pull]), RecordType.TICKET)

    jira = _JIRA_PATH.match(path)
    if jira:
        prefix, key = jira.groups()
        browse = f"{origin}{prefix}/browse/{key}"
        return LinkTarget(browse, _dedup([browse]), RecordType.TICKET)

    confluence = _CONFLUENCE_PATH.match(path)
    if confluence:
        page = f"{origin}{confluence.group(1)}"
        as_pasted = f"{origin}{path.rstrip('/')}"
        return LinkTarget(page, _dedup([as_pasted, page]), RecordType.CONFLUENCE_PAGE)

    contact = normalize_contact(EntityKind.URL, raw)
    if contact is None:
        return None
    canonical = contact.canonical
    return LinkTarget(
        canonical,
        _dedup([_without_fragment(parts), canonical, canonical + "/"]),
        RecordType.WEBPAGE,
    )


def addresses_linking_to(weburl: str | None) -> tuple[str, ...]:
    """The addresses a link to the record stored at ``weburl`` is usually pasted
    as. Used from the record's side: when it is indexed after the link was."""
    if not isinstance(weburl, str) or not weburl:
        return ()
    target = link_target(weburl)
    if target is None:
        return ()
    stored = _clean(weburl)
    forms = [stored, target.url, target.url + "/"]
    if target.record_type is RecordType.PULL_REQUEST:
        forms.extend(f"{target.url}/{tab}" for tab in _GITHUB_PR_TABS)
    return _dedup(forms)


def related_for_urls(urls: list[str]) -> list[RelatedExternalRecord]:
    """One related record per distinct linked address, at most ``MAX_LINKED_URLS``.

    Each entry is resolved by address in any connector of the org and never
    creates a placeholder (see ``DataSourceEntitiesProcessor``).
    """
    related: list[RelatedExternalRecord] = []
    seen: set[str] = set()
    for url in urls:
        target = link_target(url)
        if target is None or target.url in seen:
            continue
        seen.add(target.url)
        related.append(RelatedExternalRecord(
            external_record_id=target.url,
            record_type=target.record_type,
            relation_type=RecordRelations.LINKED_TO,
            weburl=_clean(url),
        ))
        if len(related) >= MAX_LINKED_URLS:
            break
    return related
