"""Resolves the names a user gave in chat to what the requester can actually attach.

Pure functions over plain catalogs, so the lookups are testable without a graph or config store.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.agents.actions.agent_builder.models import (
    DraftKnowledge,
    DraftTool,
    DraftToolset,
    DraftUnresolved,
    DraftWebSearch,
)

MAX_CANDIDATES = 5
MAX_QUERY = 200

WEB_SEARCH_LABELS = {
    "duckduckgo": "DuckDuckGo",
    "serper": "Serper",
    "tavily": "Tavily",
    "exa": "Exa",
}

_STOPWORDS = frozenset({
    "a", "an", "the", "in", "on", "of", "for", "to", "with", "using", "via", "from", "and", "my", "our",
    "tool", "tools", "toolset", "action", "actions", "integration", "app",
})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class KnowledgeEntry:
    id: str
    name: str
    kind: str
    connector_type: str | None = None


def _key(text: str) -> str:
    return _NON_ALNUM.sub("_", str(text).lower()).strip("_")


def _tokens(text: str) -> list[str]:
    out = []
    for token in _key(text).split("_"):
        if not token or token in _STOPWORDS:
            continue
        out.append(token[:-1] if len(token) > 3 and token.endswith("s") else token)
    return out


def _trim(query: str) -> str:
    return str(query).strip()[:MAX_QUERY]


def _unresolved_knowledge(query: str, reason: str, candidates: Sequence[str] = ()) -> DraftUnresolved:
    return DraftUnresolved(
        kind="knowledge", query=_trim(query), reason=reason,  # type: ignore[arg-type]
        candidates=list(dict.fromkeys(candidates))[:MAX_CANDIDATES],
    )


def _match_knowledge(needle: str, catalog: Sequence[KnowledgeEntry]) -> KnowledgeEntry | list[KnowledgeEntry]:
    """One entry on a unique hit, several on an ambiguous one, none when nothing matches.
    The strictest rule that matches anything decides."""
    names = [(" ".join(e.name.casefold().split()), e) for e in catalog]
    for matches in (
        lambda n: n == needle,
        lambda n: n.startswith(needle),
        lambda n: needle in n,
    ):
        hits = [e for n, e in names if matches(n)]
        if hits:
            return hits[0] if len(hits) == 1 else hits
    return []


def resolve_knowledge(
    queries: Iterable[str], catalog: Sequence[KnowledgeEntry],
) -> tuple[list[DraftKnowledge], list[DraftUnresolved]]:
    by_id = {entry.id: entry for entry in catalog}
    resolved: dict[str, KnowledgeEntry] = {}
    unresolved: list[DraftUnresolved] = []

    for query in queries:
        needle = " ".join(str(query).casefold().split())
        if not needle:
            continue
        if query.strip() in by_id:
            entry = by_id[query.strip()]
            resolved.setdefault(entry.id, entry)
            continue
        match = _match_knowledge(needle, catalog)
        if isinstance(match, KnowledgeEntry):
            resolved.setdefault(match.id, match)
        elif match:
            unresolved.append(_unresolved_knowledge(query, "ambiguous", [h.name for h in match]))
        else:
            unresolved.append(_unresolved_knowledge(query, "not_found"))

    return (
        [
            DraftKnowledge(id=e.id, name=e.name, kind=e.kind, connectorType=e.connector_type)  # type: ignore[arg-type]
            for e in resolved.values()
        ],
        unresolved,
    )


def external_toolsets(
    toolsets: Iterable[Mapping[str, Any]], internal_names: Iterable[str],
) -> list[Mapping[str, Any]]:
    """Drops toolsets the registry flags internal; they are backend plumbing, never something to attach."""
    internal = {_key(n) for n in internal_names}
    return [
        t for t in toolsets
        if t.get("instanceId") and _key(str(t.get("name") or t.get("toolsetType") or "")) not in internal
    ]


def toolset_label(toolset: Mapping[str, Any]) -> str:
    base = str(toolset.get("displayName") or toolset.get("name") or "")
    instance = toolset.get("instanceName")
    return f"{base} ({instance})" if instance and instance != base else base


def _tools_of(toolset: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [t for t in toolset.get("tools") or [] if isinstance(t, Mapping) and t.get("name")]


def _full_key(toolset: Mapping[str, Any], tool: Mapping[str, Any]) -> str:
    return _key(str(tool.get("fullName") or f"{toolset.get('name')}.{tool.get('name')}"))


def _tool_matches(
    query: str, toolsets: Sequence[Mapping[str, Any]],
) -> tuple[list[tuple[Mapping[str, Any], Mapping[str, Any]]], bool]:
    """Tool-level hits as (toolset, tool) pairs, and whether the query targeted a whole toolset instead."""
    qkey = _key(query)
    pairs = [(ts, tool) for ts in toolsets for tool in _tools_of(ts)]

    exact = [(ts, tool) for ts, tool in pairs if _full_key(ts, tool) == qkey]
    if exact:
        return exact, False

    asked = {qkey, "_".join(_tokens(query))} - {""}
    whole = [
        ts for ts in toolsets
        if asked & {_key(str(ts.get(field) or "")) for field in ("name", "displayName", "instanceName")}
    ]
    if whole:
        return [(ts, tool) for ts in whole for tool in _tools_of(ts)], True

    bare = [(ts, tool) for ts, tool in pairs if _key(str(tool.get("name"))) == qkey]
    if bare:
        return bare, False

    wanted = set(_tokens(query))
    if not wanted:
        return [], False
    loose = []
    for ts, tool in pairs:
        ts_tokens = set(_tokens(str(ts.get("name") or ""))) | set(_tokens(str(ts.get("displayName") or "")))
        tool_tokens = set(_tokens(str(tool.get("name"))))
        if tool_tokens and tool_tokens <= wanted and ts_tokens & wanted:
            loose.append((ts, tool))
    return loose, False


def resolve_tools(
    queries: Iterable[str],
    toolsets: Sequence[Mapping[str, Any]],
    registry_toolsets: Iterable[Mapping[str, Any]] = (),
) -> tuple[list[DraftToolset], list[DraftUnresolved]]:
    """`toolsets` are the requester's authenticated toolsets; `registry_toolsets` ({name, display_name})
    only distinguish "exists but not connected" from "unknown"."""
    picked: dict[str, dict[str, Any]] = {}
    unresolved: list[DraftUnresolved] = []

    def add(ts: Mapping[str, Any], tools: Iterable[Mapping[str, Any]]) -> None:
        entry = picked.setdefault(str(ts["instanceId"]), {"toolset": ts, "tools": {}})
        for tool in tools:
            full = str(tool.get("fullName") or f"{ts.get('name')}.{tool.get('name')}")
            entry["tools"].setdefault(full, tool)

    for query in queries:
        if not _key(query):
            continue
        hits, whole_toolset = _tool_matches(query, toolsets)
        if not hits:
            registered = {
                _key(str(r.get(field) or "")) for r in registry_toolsets for field in ("name", "display_name")
            } - {""}
            wanted = {_key(query), " ".join(_tokens(query)).replace(" ", "_")}
            reason = "not_connected" if wanted & registered or set(_tokens(query)) & registered else "not_found"
            unresolved.append(DraftUnresolved(kind="tool", query=_trim(query), reason=reason))  # type: ignore[arg-type]
            continue
        if whole_toolset:
            for ts in {id(ts): ts for ts, _ in hits}.values():
                add(ts, _tools_of(ts))
            continue
        if len({_full_key(ts, tool) for ts, tool in hits}) > 1 or len({str(ts["instanceId"]) for ts, _ in hits}) > 1:
            unresolved.append(DraftUnresolved(
                kind="tool", query=_trim(query), reason="ambiguous",
                candidates=list(dict.fromkeys(
                    f"{toolset_label(ts)}: {tool.get('name')}" for ts, tool in hits
                ))[:MAX_CANDIDATES],
            ))
            continue
        add(hits[0][0], [hits[0][1]])

    out = [
        DraftToolset(
            instanceId=str(e["toolset"]["instanceId"]),
            instanceName=e["toolset"].get("instanceName"),
            name=str(e["toolset"].get("name") or e["toolset"].get("toolsetType") or ""),
            displayName=str(e["toolset"].get("displayName") or e["toolset"].get("name") or ""),
            iconPath=str(e["toolset"].get("iconPath") or ""),
            category=str(e["toolset"].get("category") or "app"),
            tools=[
                DraftTool(
                    name=str(t["name"]),
                    fullName=str(t.get("fullName") or f"{e['toolset'].get('name')}.{t['name']}"),
                    description=str(t.get("description") or ""),
                )
                for t in e["tools"].values()
            ],
        )
        for e in picked.values()
    ]
    return out, unresolved


def resolve_web_search(
    config: Mapping[str, Any] | None, supported: Iterable[str],
) -> tuple[DraftWebSearch | None, DraftUnresolved | None]:
    """`config` is the org's default provider (`{"provider": ...}`), or None when it could not be read."""
    provider = str((config or {}).get("provider") or "").strip().lower()
    if provider and provider in set(supported):
        return DraftWebSearch(provider=provider, providerLabel=WEB_SEARCH_LABELS.get(provider, provider)), None
    return None, DraftUnresolved(kind="webSearch", query="web search", reason="unavailable")


def summarize_unresolved(items: Sequence[DraftUnresolved]) -> str:
    lines = []
    for item in items:
        if item.kind == "webSearch":
            lines.append("web search: no supported provider is available")
        elif item.reason == "ambiguous":
            lines.append(f'{item.kind} "{item.query}": matches several ({", ".join(item.candidates)}); ask which one')
        elif item.reason == "not_connected":
            lines.append(f'{item.kind} "{item.query}": that app is not connected for the user')
        else:
            lines.append(f'{item.kind} "{item.query}": nothing the user can access has that name')
    return "; ".join(lines)
