"""Detection of templating dialects embedded in ``.yaml`` files.

Helm charts (Go ``text/template``), Ansible roles (Jinja2), Rails configs
(ERB) and Mustache/Handlebars templates all emit YAML but are not YAML:
``{{ .Values.x }}`` at the start of a value opens a YAML flow mapping and
``{% if %}`` / ``<% end %>`` lines are not YAML at all. PyYAML rejects
them, so :class:`YAMLParser` falls back to the structural parser in
:mod:`app.modules.parsers.yaml.structural_yaml_parser`, which needs to know
the tag delimiters and the scope keywords of the dialect in use.

Detection is content-based and deliberately conservative: it only reports
a dialect when a tag appears in a position that breaks YAML (a line that is
nothing but tags, or a tag starting an unquoted key or value). Tags that
live inside quoted strings -- ``"{{ .Release.Name }}-redis"``, GitHub
Actions ``${{ github.sha }}`` -- parse fine as YAML and are not evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "DIALECTS",
    "ControlTag",
    "DialectSpec",
    "TemplateDialect",
    "detect_template_dialect",
]


class TemplateDialect(str, Enum):
    GO = "go"              # Helm, Helmfile, consul-template, Argo Workflows
    JINJA = "jinja"        # Ansible, SaltStack, cookiecutter
    ERB = "erb"            # Rails, Chef, Puppet
    MUSTACHE = "mustache"  # Mustache, Handlebars


@dataclass(frozen=True)
class ControlTag:
    """A tag that opens, continues or closes a template scope."""

    role: str   # "open" | "middle" | "close"
    label: str  # human-readable expression, e.g. ``if .Values.ingress.enabled``


@dataclass(frozen=True)
class DialectSpec:
    dialect: TemplateDialect
    tag: re.Pattern[str]
    # (open, close) delimiter pairs, used to join tags that span lines.
    delimiters: tuple[tuple[str, str], ...]
    classify: Callable[[str], ControlTag | None]


def _strip(tag: str, opener: str, closer: str) -> str:
    body = tag[len(opener):-len(closer)]
    return " ".join(body.strip("-~ \t\n").split())


_GO_OPEN = frozenset({"if", "with", "range", "define", "block"})
_JINJA_OPEN = frozenset({"if", "for", "macro", "block", "raw", "filter", "call"})
_JINJA_MIDDLE = frozenset({"elif", "else"})
_ERB_OPEN = re.compile(r"^(if|unless|case|while|until|begin)\b")
_ERB_DO = re.compile(r"\bdo(\s*\|[^|]*\|)?\s*$")
_ERB_MIDDLE = re.compile(r"^(else|elsif|when)\b")


def _classify_go(tag: str) -> ControlTag | None:
    expr = _strip(tag, "{{", "}}")
    if expr.startswith("/*"):
        return None
    word = expr.split(" ", 1)[0]
    if word in _GO_OPEN:
        return ControlTag("open", expr)
    if word == "else":
        return ControlTag("middle", expr)
    if word == "end":
        return ControlTag("close", expr)
    return None


def _classify_jinja(tag: str) -> ControlTag | None:
    if not tag.startswith("{%"):
        return None
    expr = _strip(tag, "{%", "%}")
    word = expr.split(" ", 1)[0]
    if word in _JINJA_OPEN:
        return ControlTag("open", expr)
    if word in _JINJA_MIDDLE:
        return ControlTag("middle", expr)
    if word.startswith("end"):
        return ControlTag("close", expr)
    return None


def _classify_erb(tag: str) -> ControlTag | None:
    if tag.startswith(("<%=", "<%#")):
        return None
    expr = _strip(tag, "<%", "%>")
    if _ERB_MIDDLE.match(expr):
        return ControlTag("middle", expr)
    if re.match(r"^end\b", expr):
        return ControlTag("close", expr)
    # ``<% if x then y end %>`` is self-contained; only an unterminated
    # ``if``/``do`` opens a scope.
    if (_ERB_OPEN.match(expr) or _ERB_DO.search(expr)) and not re.search(r"\bend$", expr):
        return ControlTag("open", expr)
    return None


def _classify_mustache(tag: str) -> ControlTag | None:
    expr = _strip(tag, "{{", "}}")
    if expr.startswith("#"):
        return ControlTag("open", expr[1:].strip())
    if expr.startswith("^"):
        return ControlTag("open", "not " + expr[1:].strip() if expr[1:].strip() else "else")
    if expr.startswith("/"):
        return ControlTag("close", expr[1:].strip())
    if expr == "else" or expr.startswith("else "):
        return ControlTag("middle", expr)
    return None


DIALECTS: dict[TemplateDialect, DialectSpec] = {
    TemplateDialect.GO: DialectSpec(
        TemplateDialect.GO,
        re.compile(r"\{\{.*?\}\}", re.S),
        (("{{", "}}"),),
        _classify_go,
    ),
    TemplateDialect.JINJA: DialectSpec(
        TemplateDialect.JINJA,
        re.compile(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", re.S),
        (("{%", "%}"), ("{{", "}}"), ("{#", "#}")),
        _classify_jinja,
    ),
    TemplateDialect.ERB: DialectSpec(
        TemplateDialect.ERB,
        re.compile(r"<%.*?%>", re.S),
        (("<%", "%>"),),
        _classify_erb,
    ),
    TemplateDialect.MUSTACHE: DialectSpec(
        TemplateDialect.MUSTACHE,
        re.compile(r"\{\{\{.*?\}\}\}|\{\{.*?\}\}", re.S),
        (("{{", "}}"),),
        _classify_mustache,
    ),
}

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_GHA_EXPR = re.compile(r"\$\{\{.*?\}\}")
_ANY_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|<%.*?%>")
_JINJA_STMT = re.compile(r"\{%.*?%\}")
_ERB_TAG = re.compile(r"<%.*?%>")
_MUSTACHE_SECTION = re.compile(r"\{\{[#/^]")
_GO_MARKERS = re.compile(
    r"\{\{-|-\}\}|\.Values\b|\.Release\b|\.Chart\b|\.Capabilities\b"
    r"|\binclude \"|\btemplate \"|\btoYaml\b|\bnindent\b|\{\{\s*-?\s*(end|if|range|with|define)\b"
)
# A tag where YAML expects a plain scalar to start: at line start (after
# optional ``- `` list markers) or right after ``key: ``. Quoted positions
# are excluded because YAML parses those.
_BARE_TAG_POSITION = re.compile(
    r"^\s*(?:-\s+)*(?:[^\s\"'#{<][^:]*:\s+)?(?:\{\{|\{%|<%)"
)


def _is_yaml_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _structural_evidence(text: str) -> int:
    """Count lines where a template tag sits in a position YAML cannot parse."""
    count = 0
    for raw in text.splitlines():
        if not raw.strip() or _is_yaml_comment(raw):
            continue
        line = _GHA_EXPR.sub("", raw)
        if not _ANY_TAG.search(line) and not line.lstrip().startswith(("{{", "{%", "<%")):
            continue
        if _ANY_TAG.sub("", line).strip() == "" or _BARE_TAG_POSITION.match(line):
            count += 1
    return count


def detect_template_dialect(text: str) -> TemplateDialect | None:
    """Return the dialect whose tags make *text* unparseable as YAML, else None.

    Precedence resolves the shared ``{{ }}`` syntax: ``{% %}`` only exists in
    Jinja, ``<% %>`` only in ERB, ``{{# }}``/``{{/ }}`` only in Mustache.
    Anything else with ``{{ }}`` is treated as Go; when no control tags are
    present the choice does not affect the output.
    """
    if _structural_evidence(text) == 0:
        return None
    scan = _GHA_EXPR.sub("", text)
    if _JINJA_STMT.search(scan):
        return TemplateDialect.JINJA
    if _ERB_TAG.search(scan):
        return TemplateDialect.ERB
    if _MUSTACHE_SECTION.search(scan) and not _GO_MARKERS.search(scan):
        return TemplateDialect.MUSTACHE
    if "{{" in scan:
        return TemplateDialect.GO
    return None
