"""Search/pagination helpers over the MCP registry catalog — kept separate from
`registry.py` (which only owns discovery/storage) so routes stay thin.

Search ranks by name first, then by what a server does (its `capabilities`), its category and
its description. People describe a job ("schedule meetings", "support tickets") more often than
they know a product's name, so a few common synonyms widen each word.
"""
import re
from typing import NamedTuple, Optional

from app.agents.mcp.models import MCPCategory, MCPServerTemplate

DEFAULT_PAGE = 1
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# The category names search matches (the UI translates its own).
CATEGORY_LABELS: dict[MCPCategory, str] = {
    MCPCategory.COMMUNICATION: "Communication",
    MCPCategory.MEETINGS: "Meetings & calendar",
    MCPCategory.DOCS: "Docs & files",
    MCPCategory.PROJECTS: "Project management",
    MCPCategory.DEVELOPER: "Developer tools",
    MCPCategory.DATA: "Data & analytics",
    MCPCategory.SALES: "Sales & CRM",
    MCPCategory.SUPPORT: "Customer support",
    MCPCategory.FINANCE: "Finance & legal",
    MCPCategory.DESIGN: "Design & whiteboards",
    MCPCategory.AUTOMATION: "Automation",
    MCPCategory.SEARCH: "Search & research",
    MCPCategory.SECURITY: "Security & identity",
}

_STOP_WORDS = frozenset(
    "i a an the to for my our we need want something that can with and of in on from help me "
    "tool tools app apps server servers mcp do does some any let lets".split()
)
SYNONYMS: dict[str, tuple[str, ...]] = {
    "ticket": ("issue", "support", "task"), "bug": ("issue", "error"),
    "meeting": ("calendar", "call", "transcript", "zoom"), "call": ("meeting", "transcript"),
    "schedule": ("calendar", "meeting", "event"), "calendar": ("event", "schedule"), "availability": ("free time",),
    "email": ("mail", "gmail"), "mail": ("email",),
    "doc": ("page", "file", "document"), "docs": ("page", "file", "document", "documentation"),
    "document": ("file", "page"), "wiki": ("page", "confluence", "notion"),
    "deploy": ("deployment", "worker"), "release": ("deployment", "pull request"),
    "crm": ("contact", "deal", "lead"), "customer": ("contact", "support", "conversation"),
    "sign": ("signature",), "signature": ("agreement",), "contract": ("agreement", "signature"),
    "invoice": ("billing", "payment"), "billing": ("invoice", "payment", "subscription"), "refund": ("payment",),
    "chat": ("message", "channel", "conversation"), "message": ("chat", "channel"),
    "analytics": ("metric", "query", "insight"), "metric": ("analytics",),
    "diagram": ("whiteboard", "flowchart"), "whiteboard": ("diagram", "board"), "design": ("wireframe", "diagram"),
    "transcript": ("meeting", "call"), "note": ("meeting notes", "page"),
    "code": ("repository", "pull request", "library"), "pr": ("pull request", "merge request"), "repo": ("repository",),
    "dns": ("domain",), "user": ("group", "access"),
    "sales": ("deal", "lead", "pipeline"), "lead": ("contact", "campaign"), "outreach": ("campaign", "cold email"),
    "web": ("search", "page"), "research": ("search", "paper"),
    "task": ("issue", "item", "card"), "sprint": ("cycle", "issue"),
    "file": ("document", "folder"), "spreadsheet": ("table", "sheet", "base"),
}
_SUFFIX = re.compile(r"(ings|ing|ies|es|s)$")
_NOT_A_WORD = re.compile(r"[^a-z0-9.]+")

# A name hit outranks a job, a job the category, the category the description. Below this a
# result is noise.
_MIN_SCORE = 20


class CatalogMatch(NamedTuple):
    template: MCPServerTemplate
    # The capability a search matched, shown to say why a server is in the results.
    capability: Optional[str] = None


def _stem(word: str) -> str:
    return _SUFFIX.sub("", word) or word


# A synonym is a weaker hit than the word itself, so "schedule meetings" puts the calendar
# ("Schedule meetings") above a meeting recorder ("Find what was said in a meeting").
_SYNONYM_WEIGHT = 0.6


def _starts_a_word(text: str, part: str) -> bool:
    # At a word start, so "sign" doesn't match "assign".
    return re.search(r"\b" + re.escape(part), text) is not None


def _hit(text: str, candidate: str) -> bool:
    if len(candidate) < 2:
        return False
    return _starts_a_word(text, candidate) or (len(candidate) > 3 and _starts_a_word(text, _stem(candidate)))


def _weight(text: str, word: str) -> float:
    stem = _stem(word)
    if _hit(text, word) or (len(word) > 3 and _hit(text, stem)):
        return 1.0
    synonyms = (*SYNONYMS.get(word, ()), *SYNONYMS.get(stem, ()))
    return _SYNONYM_WEIGHT if any(_hit(text, s) for s in synonyms) else 0.0


def _coverage(text: str, words: list[str]) -> float:
    """How well `text` covers the words, 1 when each appears as itself."""
    text = text.casefold()
    return sum(_weight(text, w) for w in words) / len(words)


def _score(template: MCPServerTemplate, query: str) -> tuple[float, Optional[str]]:
    name = template.display_name.casefold()
    if query in (name, template.type_id):
        return 100, None
    if name.startswith(query):
        return 92, None
    if query in name or query in template.type_id:
        return 85, None
    words = [w for w in _NOT_A_WORD.split(query) if w and w not in _STOP_WORDS]
    if not words:
        return 0, None
    best, capability = 0.0, None
    for cap in template.capabilities:
        f = _coverage(cap, words)
        if f > best:
            best, capability = f, cap
    category = CATEGORY_LABELS.get(template.category, "") if template.category else ""
    score = 80.0 if _coverage(template.display_name, words) == 1 else 0.0
    if best >= 0.5:
        score = max(score, 50 + best * 20)
    if category and _coverage(category, words) >= 0.8:
        score = max(score, 40)
    # Descriptions are long and loose, so only every word appearing as itself counts.
    if _coverage(" ".join([template.description, *template.tags]), words) == 1:
        score = max(score, 35)
    return score, capability if best >= 0.5 else None


def rank_templates(
    templates: list[MCPServerTemplate], search: str | None, category: str | None = None,
) -> list[CatalogMatch]:
    """`templates` in `category` (when given) that match `search`, best first, each with the
    capability that matched. Without a search they keep the registry's order."""
    shelf = [t for t in templates if not category or (t.category is not None and t.category.value == category)]
    query = (search or "").strip().casefold()
    if not query:
        return [CatalogMatch(t) for t in shelf]
    scored = [(t, *_score(t, query)) for t in shelf]
    kept = [(t, s, cap) for t, s, cap in scored if s >= _MIN_SCORE]
    kept.sort(key=lambda item: (-item[1], item[0].display_name.casefold()))
    return [CatalogMatch(t, cap) for t, _, cap in kept]


def search_templates(templates: list[MCPServerTemplate], search: str | None) -> list[MCPServerTemplate]:
    """`rank_templates` without the reasons."""
    return [m.template for m in rank_templates(templates, search)]


def paginate(items: list, page: int | None, limit: int | None) -> tuple[list, int]:
    """Slice `items` for the given 1-indexed page/limit. Returns (page_items, total_count)."""
    total = len(items)
    page = max(1, page or DEFAULT_PAGE)
    limit = min(MAX_LIMIT, max(1, limit or DEFAULT_LIMIT))
    start = (page - 1) * limit
    end = start + limit
    return items[start:end], total
