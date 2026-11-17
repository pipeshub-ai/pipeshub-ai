"""Pin the load alert and indexing back-off settings, and the alert titles,
to what the deployment docs say, so the code and the docs cannot drift.

The docs are parsed, not copied: a change on either side fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from app.services.resource_governor import yield_signal as ys
from app.services.sandbox_alerts import config as cfg
from app.services.sandbox_alerts import event as ev
from app.services.sandbox_alerts.config import AlertSettings

ROOT = Path(__file__).resolve().parents[7]
ADVANCED = ROOT / "deployment/docker-compose/ADVANCED_DEPLOYMENT.md"
BACKEND_ENV = ROOT / "backend/env.template"
COMPOSE_ENV = ROOT / "deployment/docker-compose/env.template"
HELM_README = ROOT / "deployment/helm/pipeshub-ai/README.md"
HELM_DEPLOYMENT = ROOT / "deployment/helm/pipeshub-ai/templates/deployment.yaml"
NODE_MAIL = ROOT / "backend/nodejs/apps/src/modules/notification/utils/sandbox-capacity-mail.ts"
NODE_ALERT = ROOT / "backend/nodejs/apps/src/modules/notification/utils/sandbox-capacity-alert.ts"
COMPOSE_FILES = [
    ROOT / "deployment/docker-compose" / name
    for name in (
        "docker-compose.yml",
        "docker-compose.prod-legacy.yml",
        "docker-compose.build.neo4j.yml",
        "docker-compose.integration.arango.yml",
        "docker-compose.integration.neo4j.yml",
        "beta/docker-compose.build.neo4j.slim.yml",
        "beta/docker-compose.build.opensearch.neo4j.yml",
    )
]

# Each documented variable: the AlertSettings / LiveSettings field it sets.
ALERT_FIELDS = {
    cfg.ENV_ENABLED: "enabled",
    cfg.ENV_WINDOW_S: "window_s",
    cfg.ENV_EVAL_S: "eval_s",
    cfg.ENV_WARN_BUSY_PCT: "warn_busy_pct",
    cfg.ENV_WARN_MIN_WAITED: "warn_min_waited",
    cfg.ENV_CRIT_MIN_REFUSED: "crit_min_refused",
    cfg.ENV_CRIT_REFUSED_PCT: "crit_refused_pct",
    cfg.ENV_COOLDOWN_S: "cooldown_s",
    cfg.ENV_SCOPE: "scope",
}
YIELD_FIELDS = {"SANDBOX_YIELD_INDEXING": "yield_indexing", "SANDBOX_YIELD_TTL_S": "yield_ttl_s"}
_RANGE = re.compile(r"\((\d+(?:\.\d+)?)[–.-]+(\d+(?:\.\d+)?)\)")


def _table() -> dict[str, tuple[str, str]]:
    """``| `VAR` | default | meaning |`` rows of ADVANCED_DEPLOYMENT.md."""
    rows = {}
    for line in ADVANCED.read_text().splitlines():
        match = re.match(r"\|\s*`([A-Z0-9_]+)`\s*\|\s*([^|]*?)\s*\|\s*(.*?)\s*\|\s*$", line)
        if match:
            rows[match.group(1)] = (match.group(2), match.group(3))
    return rows


def _commented_defaults(path: Path) -> dict[str, str]:
    """``# VAR=value`` lines of an env template."""
    found = {}
    for line in path.read_text().splitlines():
        match = re.match(r"#\s*([A-Z][A-Z0-9_]+)=(.*)$", line.strip())
        if match:
            found[match.group(1)] = match.group(2).strip()
    return found


def _as_value(raw: str, current: object) -> object:
    if isinstance(current, bool):
        return raw.strip().lower() in {"true", "on"}
    if isinstance(current, (int, float)):
        return float(raw)
    return raw


def _field_value(name: str, settings: object, fields: dict[str, str]) -> object:
    value = getattr(settings, fields[name])
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value


TABLE = _table()
BACKEND_DEFAULTS = _commented_defaults(BACKEND_ENV)


@pytest.mark.parametrize("name", sorted(ALERT_FIELDS))
def test_every_alert_variable_is_in_the_table_with_its_real_default(name: str) -> None:
    assert name in TABLE, f"{name} missing from the Load alerts table"
    default, _ = TABLE[name]
    settings = AlertSettings.from_env({})
    assert _as_value(default, getattr(settings, ALERT_FIELDS[name])) == _field_value(name, settings, ALERT_FIELDS)
    # Blank (as Compose passes an unset variable) is the default too.
    assert AlertSettings.from_env({name: ""}) == settings == AlertSettings()


@pytest.mark.parametrize("name", sorted(YIELD_FIELDS))
def test_every_back_off_variable_is_in_the_table_with_its_real_default(name: str) -> None:
    assert name in TABLE
    default, _ = TABLE[name]
    settings = LiveSettings.from_env({})
    assert _as_value(default, getattr(settings, YIELD_FIELDS[name])) == _field_value(name, settings, YIELD_FIELDS)


@pytest.mark.parametrize("name", sorted(ALERT_FIELDS) + sorted(YIELD_FIELDS))
def test_the_backend_env_template_shows_the_same_default(name: str) -> None:
    assert name in BACKEND_DEFAULTS, f"{name} missing from backend/env.template"
    assert BACKEND_DEFAULTS[name] == TABLE[name][0]


def test_the_compose_env_template_shows_alerts_on() -> None:
    assert _commented_defaults(COMPOSE_ENV).get(cfg.ENV_ENABLED) == TABLE[cfg.ENV_ENABLED][0] == "true"


@pytest.mark.parametrize("name", [n for n in sorted(ALERT_FIELDS) if _RANGE.search(TABLE.get(n, ("", ""))[1])])
def test_documented_ranges_are_the_clamps(name: str) -> None:
    low, high = (float(x) for x in _RANGE.search(TABLE[name][1]).groups())
    field = ALERT_FIELDS[name]
    assert getattr(AlertSettings.from_env({name: str(low - 1)}), field) == low
    assert getattr(AlertSettings.from_env({name: str(high + 1)}), field) == high
    assert getattr(AlertSettings.from_env({name: str(low)}), field) == low
    assert getattr(AlertSettings.from_env({name: str(high)}), field) == high


def test_the_backend_env_template_ranges_match_the_table() -> None:
    text = BACKEND_ENV.read_text()
    for name in (cfg.ENV_WINDOW_S, cfg.ENV_EVAL_S, cfg.ENV_COOLDOWN_S):
        match = re.search(re.escape(name) + r"[^()=]{0,40}?\((\d+)\.\.(\d+)\)", text)
        assert match, f"{name} range missing from backend/env.template"
        assert _RANGE.search(TABLE[name][1]).groups() == match.groups()


def test_the_yield_ttl_range_fits_the_signal() -> None:
    low, high = (float(x) for x in _RANGE.search(TABLE["SANDBOX_YIELD_TTL_S"][1]).groups())
    assert LiveSettings.from_env({"SANDBOX_YIELD_TTL_S": str(high + 50)}).yield_ttl_s == high
    assert LiveSettings.from_env({"SANDBOX_YIELD_TTL_S": str(low - 5)}).yield_ttl_s == low
    # The signal never sets an expiry longer than the largest documented TTL.
    assert ys.MAX_SPAN_S == high
    assert ys.DEFAULT_TTL_S == float(TABLE["SANDBOX_YIELD_TTL_S"][0])


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_every_compose_file_passes_each_variable_blank_by_default(path: Path) -> None:
    text = path.read_text()
    for name in [*ALERT_FIELDS, *YIELD_FIELDS]:
        assert f"- {name}=${{{name}:-}}" in text, f"{path.name} does not pass {name}"


def test_the_chart_scopes_the_cooldown_to_the_pod() -> None:
    template = HELM_DEPLOYMENT.read_text()
    assert re.search(
        r"- name: SANDBOX_ALERT_SCOPE\s+valueFrom:\s+fieldRef:\s+fieldPath: metadata\.name", template,
    )
    readme = HELM_README.read_text()
    assert "SANDBOX_ALERT_SCOPE" in readme and "pod name" in readme


def test_the_prose_numbers_match_the_defaults() -> None:
    text = ADVANCED.read_text()
    section = text[text.index("### Load alerts"):]
    section = section[: section.index("| Variable |")]
    s = AlertSettings()
    assert f"last {s.window_s // 60} minutes" in section
    assert "every minute" in section and s.eval_s == 60
    assert f"at least {round(s.warn_busy_pct)} % of the window" in section
    assert f"at least {s.warn_min_waited} runs waited" in section
    assert f"at least {s.crit_min_refused} runs were refused" in section
    assert f"at least {round(s.crit_refused_pct)} % of all runs" in section
    assert "at most once a day" in section and s.cooldown_s == 86400
    for path in (BACKEND_ENV, HELM_README):
        assert "once a day" in path.read_text() or str(s.cooldown_s) in path.read_text()


@pytest.mark.parametrize(
    ("label", "title"),
    [("Warning", ev._TITLES["warning"]), ("Critical", ev._TITLES["critical"])],
)
def test_alert_titles_are_the_documented_ones(label: str, title: str) -> None:
    assert f'**{label}** ("{title}")' in ADVANCED.read_text()


def test_the_back_to_normal_title_is_the_documented_one() -> None:
    assert f'"{ev.RESOLVED_TITLE}" notice' in ADVANCED.read_text()


def test_the_email_wording_and_link_match_the_in_app_titles() -> None:
    node = NODE_MAIL.read_text()
    for level, title in ev._TITLES.items():
        match = re.search(rf"{level}:\s*'([^']+)'", node)
        assert match, f"no {level} summary in the alert email"
        assert match.group(1).replace(" in chat", "") == title[0].lower() + title[1:]
    assert f"'{ev.DOCS_URL}'" in NODE_ALERT.read_text()


def test_node_builds_the_documented_titles() -> None:
    """Node writes the titles admins see; they must be the documented ones."""
    node = NODE_ALERT.read_text()
    titles = dict(ev._TITLES, resolved=ev.RESOLVED_TITLE)
    for kind, title in titles.items():
        assert re.search(rf"\b{kind}:\s*'{re.escape(title)}'", node), f"Node has no {kind} title {title!r}"


def test_the_slot_wait_default_matches_the_docs() -> None:
    assert _commented_defaults(BACKEND_ENV)["SANDBOX_SLOT_WAIT_S"] == str(ev.slot_wait_seconds({}))
