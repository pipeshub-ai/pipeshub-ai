"""The operator guide and its runbooks cite only what exists (WP-87: C6.43, C15.04, C15.05, C15.06, C18.09, C19.06).

An operator runs a runbook's commands during an incident, when a wrong name costs the most. Every inline and
fenced code span of the operator documents is classified (environment variable, log event, metric, HTTP
route, `python -m` module, Node CLI, script, installer flag, Compose service) and each must resolve to the code
that defines it: an env var some shipped code or deployment file reads, a log event or metric a source file
spells as a string, a route in the OpenAPI document or a source file, a module or script on disk, a flag
``install.sh`` parses, a service a Compose file declares. A span the classifier does not recognise (a value, a
label in the app) is left alone. The changelog's harness entry must name exactly the allow-listed engine
versions. ``test_wp87_doc_2_*`` proves the checker itself catches each kind of invented name.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

import pytest

from tests.cohesion.support import REPO_ROOT

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

GUIDES = REPO_ROOT / "docs" / "agent-harness-v2" / "guides"
OPERATOR_DOCS = (
    GUIDES / "operator-guide.md",
    GUIDES / "runbooks.md",
    GUIDES / "sizing.md",
    GUIDES / "air-gapped-install.md",
)
RUNBOOK_SECTIONS = (
    "## Incident response",
    "## Stuck session",
    "## Exhausted pool",
    "## Gateway 5xx spike",
    "## Harness CVE response",
    "## Credential rotation",
    "## Approval backlog",
)

# Where a name counts as defined: shipped code and deployment files, never docs or tests.
SOURCE_ROOTS = (
    "backend/python/app",
    "backend/python/harness_core",
    "backend/python/harness_engines",
    "backend/nodejs/apps/src",
    "backend/env.template",
    "deployment",
    "contracts",
    "scripts",
    "install.sh",
    "Dockerfile",
)
SOURCE_SUFFIXES = {".py", ".ts", ".js", ".mjs", ".sh", ".yml", ".yaml", ".json", ".template", ".toml", ".tpl", ".conf"}
SKIP_PARTS = {"node_modules", "tests", "__pycache__", "dist", "generated", "test"}

CODE_FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.S)
INLINE = re.compile(r"`([^`\n]+)`")

ENV = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
EVENT = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
METRIC = re.compile(r"pipeshub_[a-z0-9_]+")
ROUTE = re.compile(r"(?:(GET|POST|PUT|PATCH|DELETE) )?(?<![\w/.~-])(/(?:api|v1|healthz|metrics|harness)[A-Za-z0-9_\-/{}:.…]*)")
PY_MODULE = re.compile(r"python[0-9.]* -m ([a-z_][a-z0-9_.]*)")
NODE_CLI = re.compile(r"node (dist/[A-Za-z0-9_\-/.]+\.js)")
SCRIPT = re.compile(r"(?<![\w/.-])((?:scripts|deployment)/[A-Za-z0-9_\-/.]+\.(?:sh|py))")
INSTALL_FLAG = re.compile(r"install\.sh((?: --[a-z][a-z-]*)+)")
COMPOSE = re.compile(
    r"docker compose(?: (?:-f|-p|--profile) \S+)* (?:logs|restart|stop|up|exec|kill|ps)(?: -[-A-Za-z]+)* ([a-z][a-z0-9-]+)"
)

# File names and other dotted words that are not log events.
NOT_EVENTS = re.compile(r"\.(?:md|json|yml|yaml|py|ts|tsx|js|sh|txt|lock|log|toml|html|env|tar|gz|io|com|ai|local)$")


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    name: str


def _spans(text: str) -> list[str]:
    """Every code span: fenced blocks line by line, then inline spans outside them."""
    spans: list[str] = []
    for block in CODE_FENCE.findall(text):
        spans.extend(line.strip() for line in block.splitlines() if line.strip())
    spans.extend(INLINE.findall(CODE_FENCE.sub("", text)))
    return spans


@cache
def _source_text() -> str:
    chunks: list[str] = []
    for root in SOURCE_ROOTS:
        path = REPO_ROOT / root
        files: Iterable[Path] = [path] if path.is_file() else path.rglob("*")
        for file in files:
            if not file.is_file() or SKIP_PARTS & set(file.relative_to(REPO_ROOT).parts):
                continue
            if file.suffix not in SOURCE_SUFFIXES and not file.name.startswith(("Dockerfile", "docker-compose")):
                continue
            if file.stat().st_size > 2_000_000:
                continue
            chunks.append(file.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


@cache
def _source_words() -> frozenset[str]:
    return frozenset(re.findall(r"[A-Za-z0-9_]+", _source_text()))


@cache
def _string_literals() -> frozenset[str]:
    return frozenset(re.findall(r"""["'`]([^"'`\n]{1,160})["'`]""", _source_text()))


def _route_shape(path: str) -> str:
    """A route with its parameters made one token, so `{id}`, `:id` and `<id>` compare equal."""
    path = path.split("?", 1)[0].rstrip("/.…") or "/"
    return re.sub(r"\{[^}]+\}|:[A-Za-z_]+|<[^>]+>", "{}", path)


@cache
def _routes() -> frozenset[str]:
    spec = (REPO_ROOT / "backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml").read_text(encoding="utf-8")
    known = {_route_shape(p) for p in re.findall(r"^  (/[^:\s]*):\s*$", spec, re.M)}
    # Internal listeners (egress proxy, broker, gateways, Node's internal port) are not in the public spec.
    known |= {_route_shape(lit) for lit in _string_literals() if lit.startswith("/")}
    return frozenset(known)


def _route_known(path: str) -> bool:
    shape = _route_shape(path)
    if shape in _routes():
        return True
    # A route the doc abbreviates with `…` names a prefix of a real one.
    if "…" in path:
        prefix = _route_shape(path.split("…", 1)[0])
        return any(r.startswith(prefix) for r in _routes())
    # Node routers mount relative paths under a prefix: accept a known route that ends with this one's tail.
    parts = shape.strip("/").split("/")
    return any("/" + "/".join(parts[i:]) in _routes() for i in range(2, max(3, len(parts) - 1)))


@cache
def _install_flags() -> frozenset[str]:
    text = (REPO_ROOT / "deployment/docker-compose/install.sh").read_text(encoding="utf-8")
    return frozenset(re.findall(r"^\s+(?:-[a-z]\|)?(--[a-z][a-z-]*)\)", text, re.M))


@cache
def _compose_services() -> frozenset[str]:
    services: set[str] = set()
    for file in (REPO_ROOT / "deployment/docker-compose").glob("docker-compose*.yml"):
        services.update(re.findall(r"^  ([a-z][a-z0-9-]+):\s*$", file.read_text(encoding="utf-8"), re.M))
    return frozenset(services)


@cache
def _helm_value_paths() -> frozenset[str]:
    """Every key path of the chart's values files (`global.imageRegistry`), which a doc may name like an event."""
    import yaml

    paths: set[str] = set()

    def walk(node: object, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                path = f"{prefix}{key}"
                paths.add(path)
                walk(value, f"{path}.")

    for file in (REPO_ROOT / "deployment/helm/pipeshub-ai").glob("values*.yaml"):
        walk(yaml.safe_load(file.read_text(encoding="utf-8")), "")
    return frozenset(paths)


def _module_exists(module: str) -> bool:
    base = REPO_ROOT / "backend/python" / module.replace(".", "/")
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file() or (base / "__main__.py").is_file()


def _script_exists(script: str) -> bool:
    return (REPO_ROOT / script).is_file() or (REPO_ROOT / "backend/python" / script).is_file()


def _node_cli_exists(js: str) -> bool:
    return (REPO_ROOT / "backend/nodejs/apps/src" / js.removeprefix("dist/").removesuffix(".js")).with_suffix(".ts").is_file()


def _metric_known(name: str, words: frozenset[str]) -> bool:
    # Histograms expose `_bucket`/`_sum`/`_count`; a counter's `_total` may be added by the client.
    base = re.sub(r"_(bucket|sum|count)$", "", name)
    return any(n in words for n in (name, base, re.sub(r"_total$", "", base)))


def _is_unknown_event(span: str, literals: frozenset[str]) -> str | None:
    stripped = span.split("{", 1)[0].split("(", 1)[0].strip()
    if (
        EVENT.fullmatch(stripped)
        and not NOT_EVENTS.search(stripped)
        and not _module_exists(stripped)
        and stripped not in literals
        and stripped not in _helm_value_paths()
    ):
        return stripped
    return None


def unresolved(text: str) -> list[Finding]:
    """The names in ``text``'s code spans that nothing in the shipped code defines."""
    findings: list[Finding] = []
    words, literals = _source_words(), _string_literals()
    for span in _spans(text):
        findings.extend(Finding("env", n) for n in ENV.findall(span) if len(n) >= 6 and n not in words)
        findings.extend(Finding("metric", n) for n in METRIC.findall(span) if not _metric_known(n, words))
        if (event := _is_unknown_event(span, literals)) is not None:
            findings.append(Finding("event", event))
        findings.extend(Finding("route", p) for _, p in ROUTE.findall(span) if not _route_known(p))
        findings.extend(Finding("module", m) for m in PY_MODULE.findall(span) if not _module_exists(m))
        findings.extend(Finding("node-cli", j) for j in NODE_CLI.findall(span) if not _node_cli_exists(j))
        findings.extend(Finding("script", s) for s in SCRIPT.findall(span) if not _script_exists(s))
        for flags in INSTALL_FLAG.findall(span):
            findings.extend(Finding("install-flag", f) for f in flags.split() if f not in _install_flags())
        findings.extend(Finding("compose-service", c) for c in COMPOSE.findall(span) if c not in _compose_services())
    return findings


@pytest.mark.parametrize("doc", OPERATOR_DOCS, ids=lambda p: p.name)
def test_wp87_doc_1_every_name_an_operator_doc_cites_exists(doc: Path) -> None:
    missing = sorted({(f.kind, f.name) for f in unresolved(doc.read_text(encoding="utf-8"))})
    assert not missing, f"{doc.name} cites names no shipped code defines: {missing}"


@pytest.mark.parametrize(
    ("span", "kind"),
    [
        ("`PXH_WP87_INVENTED_SWITCH=true`", "env"),
        ("`pipeshub_wp87_invented_total`", "metric"),
        ("`wp87.invented_event`", "event"),
        ("`global.wp87_invented`", "event"),
        ("`POST /api/v1/agent-harness/wp87-invented`", "route"),
        ("`python -m app.harness.wp87_invented --once`", "module"),
        ("`node dist/modules/agent-harness/ops/wp87-invented-cli.js export`", "node-cli"),
        ("`bash scripts/wp87_invented.sh`", "script"),
        ("`./install.sh --wp87-invented`", "install-flag"),
        ("`docker compose logs wp87-invented`", "compose-service"),
        ("`docker compose --profile sandbox logs --no-color wp87-invented`", "compose-service"),
    ],
)
def test_wp87_doc_2_the_checker_catches_an_invented_name(span: str, kind: str) -> None:
    assert kind in {f.kind for f in unresolved(span)}, f"{span} was not reported as an unknown {kind}"


def test_wp87_doc_2b_the_checker_accepts_real_names() -> None:
    real = (
        "`AGENT_HARNESS_KILL_SWITCH=true` `pipeshub_agent_harness_run_claims_total` `gateway.refused` "
        "`POST /v1/revocations` `python -m app.harness.audit --once` "
        "`node dist/modules/agent-harness/ops/session-backup-cli.js export` `./install.sh --rotate-signing-secrets` "
        "`docker compose --profile sandbox logs --timestamps egress-proxy` `./install.sh --yes`"
    )
    assert unresolved(real) == []


def test_wp87_doc_3_the_runbooks_have_the_seven_sections_and_each_says_what_to_check() -> None:
    text = (GUIDES / "runbooks.md").read_text(encoding="utf-8")
    for heading in RUNBOOK_SECTIONS:
        assert heading in text, f"runbooks.md has no {heading!r}"
        body = text.split(heading, 1)[1].split("\n## ", 1)[0]
        for part in ("**Symptoms.**", "**Check.**", "**Act.**"):
            assert part in body, f"{heading}: no {part}"


def test_wp87_doc_4_the_operator_guide_links_every_operator_doc_and_the_index_links_it() -> None:
    guide = (GUIDES / "operator-guide.md").read_text(encoding="utf-8")
    for doc in OPERATOR_DOCS[1:]:
        assert f"]({doc.name}" in guide, f"operator-guide.md does not link {doc.name}"
    index = (REPO_ROOT / "docs/agent-harness-v2/README.md").read_text(encoding="utf-8")
    assert "](guides/operator-guide.md)" in index


def _allowlist() -> list[dict[str, object]]:
    raw = json.loads((REPO_ROOT / "backend/python/harness_engines/allowlist.json").read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else raw["engines"]


def test_wp87_doc_5_the_changelog_names_every_allow_listed_engine_version_and_image() -> None:
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## Unreleased", 1)[1].split("\n## ", 1)[0]
    entry = next(line for line in unreleased.splitlines() if line.startswith("- **Agent harness"))
    for engine in _allowlist():
        assert f"{engine['version']}" in entry, f"{engine['kind']} {engine['version']} is not in the changelog entry"
        assert f"`{engine['image']}`" in entry or engine["kind"] == "acp-opencode", engine["kind"]
    # A pre-release counts as its version (DeepSeek Harness 0.2.2-rc.1, WP-105).
    versions = set(re.findall(r"\*\*[A-Za-z -]+ (\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)\*\*", entry))
    assert versions == {str(e["version"]) for e in _allowlist()}, versions


def test_wp87_doc_6_the_alert_rules_name_only_log_events_and_settings_that_exist() -> None:
    """An alert's description is the first runbook an operator reads, so the names in it must exist too."""
    import yaml

    rules = yaml.safe_load((REPO_ROOT / "deployment/monitoring/prometheus/agent-harness.rules.yml").read_text())
    text = " ".join(
        str(rule.get("annotations", {}).get(part, ""))
        for group in rules["groups"]
        for rule in group["rules"]
        for part in ("summary", "description")
    )
    words = re.findall(r"(?<![\w{.$])([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)(?![\w(])", text)
    envs = [w for w in re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", text) if len(w) >= 6]
    missing = sorted({w for w in words if w not in _string_literals()} | {e for e in envs if e not in _source_words()})
    assert not missing, f"the alert rules name things the code does not log or read: {missing}"
