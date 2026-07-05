"""Path- and tag-based matching for the middleware pipeline.

Tool paths look like ``/toolsets/jira/create_issue``. Middleware can be
scoped to a subset of tools two ways:

    1. By path glob, matched against the *entire* path, segment by segment:
        - ``*``  matches exactly ONE path segment.
        - ``**`` matches ZERO OR MORE path segments (anywhere in the pattern).
        - any other segment matches literally.
    2. By tag, via `by_tag`/`by_tags`, matched against the resolved tool's
       effective tags (see `ToolCallContext.tags`). Prefer this for
       cross-cutting categorization (read/write/destructive, risk level,
       provider, ...) that isn't part of a tool's identity/address — it keeps
       the path a pure address and keeps categorization declarative and
       queryable (also usable in `ToolRegistry.discover(tags=...)`).

Patterns always match the full path — there is no implicit prefix matching
(unlike raw Express route prefixes). To scope "everything under X", write the
pattern as ``X/**`` explicitly. This keeps pattern semantics unambiguous:
there is exactly one way to read any given pattern.

Examples (all matched against ``/toolsets/jira/create_issue``):

    "/toolsets/jira/create_issue"   -> exact match
    "/toolsets/jira/*"              -> matches (one segment for '*')
    "/toolsets/*/create_issue"      -> matches
    "/toolsets/jira/**"             -> matches (arbitrary depth)
    "/toolsets/**/create_issue"     -> matches
    "/toolsets/**"                  -> matches
    "/toolsets/*"                   -> NO match (path has one more segment)
    "/toolsets/web/*"               -> NO match (wrong toolset segment)

See `by_tag`/`by_tags` below for the tag-based alternative, e.g.
``by_tag("category", "write")`` instead of a path convention that bakes
"write" into every write tool's address.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Matcher",
    "match_all",
    "to_matcher",
    "path_match",
    "prefixed",
    "by_tag",
    "by_tags",
]


@runtime_checkable
class _HasToolPath(Protocol):
    tool_path: str


Matcher = Callable[[Any], bool]
"""A predicate over a context object; typically inspects ``ctx.tool_path``."""


def _segments(path: str) -> list[str]:
    """Split a path into non-empty segments, tolerating leading/trailing slashes."""
    return [segment for segment in path.split("/") if segment]


def _match_segments(path_segments: list[str], pattern_segments: list[str]) -> bool:
    if not pattern_segments:
        return not path_segments

    head, *rest = pattern_segments

    if head == "**":
        # '**' matches zero segments (skip it) or one-or-more (consume a
        # segment and retry with '**' still active).
        if _match_segments(path_segments, rest):
            return True
        if path_segments:
            return _match_segments(path_segments[1:], pattern_segments)
        return False

    if not path_segments:
        return False

    if head == "*" or head == path_segments[0]:
        return _match_segments(path_segments[1:], rest)

    return False


def path_match(tool_path: str, pattern: str) -> bool:
    """Return True if ``tool_path`` matches the glob ``pattern``.

    See module docstring for the full semantics of ``*`` and ``**``.
    """
    return _match_segments(_segments(tool_path), _segments(pattern))


def match_all() -> Matcher:
    """A matcher that matches every context (used for global middleware)."""
    return lambda ctx: True


def to_matcher(pattern: "str | Matcher") -> Matcher:
    """Normalize a string glob pattern or a predicate callable into a Matcher.

    A plain string is treated as a glob pattern matched against
    ``ctx.tool_path``. Any other callable is assumed to already be a
    ``Matcher`` (a predicate taking the context and returning bool), which
    lets callers match on arbitrary context state, e.g.::

        pipeline.use(lambda ctx: ctx.tool_input.get("command", "").startswith("rm"), mw)
    """
    if isinstance(pattern, str):
        glob = pattern
        return lambda ctx: path_match(ctx.tool_path, glob)
    if callable(pattern):
        return pattern
    raise TypeError(
        f"expected a glob pattern (str) or predicate (callable), got {type(pattern)!r}"
    )


def prefixed(prefix: str, matcher: Matcher) -> Matcher:
    """Wrap ``matcher`` so it only fires for paths under ``prefix``.

    Used by ``Pipeline.mount()`` to compose sub-pipelines under a path
    namespace, similar to Express's ``app.use(prefix, router)``. Boundary-safe:
    prefix ``/toolsets/jira`` will not match ``/toolsets/jiralike/...``.
    """
    normalized_prefix = "/" + "/".join(_segments(prefix))

    def _match(ctx: Any) -> bool:
        path = ctx.tool_path
        if path != normalized_prefix and not path.startswith(normalized_prefix + "/"):
            return False
        return matcher(ctx)

    return _match


def by_tag(key: str, value: str) -> Matcher:
    """Match contexts whose resolved tool carries the tag ``key=value``.

    An alternative to encoding categorization (read/write/destructive,
    provider, risk level, ...) as a path segment. `ToolExecutor` attaches the
    resolved tool's effective tags (its own tags merged with its owning
    toolset's tags) onto `ctx.tags` before dispatch, so middleware can scope
    itself by tag instead of by path shape::

        kernel.on(HookEvent.PRE_TOOL_USE).use(by_tag("category", "write"), require_approval())
        kernel.on(HookEvent.PRE_TOOL_USE).use(by_tag("category", "destructive"), deny_all)

    This composes with everything else a `Matcher` composes with — `.use()`,
    `.tool()` — since it is just a predicate over the context, like a path
    glob or a custom lambda.
    """

    def _match(ctx: Any) -> bool:
        return any(tag.key == key and tag.value == value for tag in getattr(ctx, "tags", ()))

    return _match


def by_tags(tags: dict[str, str]) -> Matcher:
    """Match contexts whose resolved tool carries *all* of the given tags (AND).

    Example: ``by_tags({"provider": "atlassian", "category": "write"})``
    matches only tools tagged with both.
    """

    def _match(ctx: Any) -> bool:
        ctx_tags = {tag.key: tag.value for tag in getattr(ctx, "tags", ())}
        return all(ctx_tags.get(key) == value for key, value in tags.items())

    return _match
