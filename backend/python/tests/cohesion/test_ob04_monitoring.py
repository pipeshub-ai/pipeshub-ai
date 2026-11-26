"""OB-04, OB-05 (C9.04, C9.05): the shipped dashboards and alert rules read series the code really exports.

``deployment/monitoring/`` holds Prometheus' scrape config and alert rules and Grafana's dashboard, started only by
the opt-in ``docker-compose.monitoring.yml``. Static, no Docker and no promtool: every file parses, every metric an
expression names is registered somewhere in the product's code, every alert says how bad it is and what happened,
the alerts OB-05 asks for exist, and a deployment that does not name the overlay runs none of it.
"""

from __future__ import annotations

import json
import re
from functools import cache
from typing import Any

import pytest
import yaml

from tests.cohesion.compose_files import BASE, COMPOSE_DIR, compose_services
from tests.cohesion.support import REPO_ROOT

MONITORING = REPO_ROOT / "deployment" / "monitoring"
PROMETHEUS = MONITORING / "prometheus" / "prometheus.yml"
RULES = MONITORING / "prometheus" / "agent-harness.rules.yml"
DASHBOARD = MONITORING / "grafana" / "dashboards" / "agent-harness.json"
OVERLAY = COMPOSE_DIR / "docker-compose.monitoring.yml"
INTERNAL_METRICS_PATH = "/api/v1/agent-harness/internal/metrics"

# Where a series is registered: the Python services and Node; never the tests or these files themselves.
SOURCE_ROOTS = (
    REPO_ROOT / "backend" / "python" / "app",
    REPO_ROOT / "backend" / "python" / "harness_core",
    REPO_ROOT / "backend" / "nodejs" / "apps" / "src",
)
METRIC = re.compile(r"\bpipeshub_[a-z0-9_]+")
SUFFIXES = ("_bucket", "_count", "_sum")

# What C9.04 (OB-04) and C9.05 (OB-05) ask an operator to see and be told about.
DASHBOARD_SIGNALS = {
    "active sessions": "pipeshub_agent_harness_active_sessions",
    "pool depth": "pipeshub_harness_sandbox_warm_pool_depth",
    "claim latency": "pipeshub_agent_harness_run_claim_wait_seconds",
    "gateway errors": "pipeshub_harness_gateway_requests_total",
    "cache ratio": "pipeshub_agent_harness_model_usage_tokens_total",
    "cost per minute": "pipeshub_agent_harness_model_cost_usd_total",
    "sandbox seconds": "pipeshub_harness_sandbox_seconds_total",
    "per-run cache-read share (C5.05)": "pipeshub_agent_harness_run_cache_read_ratio",
    "availability (C20.48)": "pipeshub_agent_chat_turns_total",
    "session API availability (C20.48)": "pipeshub_agent_harness_session_api_requests_total",
}
REQUIRED_ALERTS = {
    "stranded": "pipeshub_agent_harness_stranded_runs_total",
    "quarantined": "pipeshub_agent_harness_quarantined_runs_total",
    "oldest running run age": "pipeshub_agent_harness_oldest_running_run_age_seconds",
    "stuck lease": "pipeshub_agent_harness_runs_lease_expired",
    "gateway error ratio": "pipeshub_harness_gateway_requests_total",
    "cost spike": "pipeshub_agent_harness_model_cost_usd_total",
    "warm pool empty": "pipeshub_harness_sandbox_warm_pool_depth",
    "scrape down": "pipeshub_harness_metrics_scrape_up",
    "cache-read share regression (C5.05)": "pipeshub_agent_harness_run_cache_read_ratio",
    "availability budget burn (C20.48)": "pipeshub_agent_chat_turns_total",
    "session API budget burn (C20.48)": "pipeshub_agent_harness_session_api_requests_total",
    "sign-in failures (SEC-D-13)": "pipeshub_auth_attempts_total",
    "egress refusals (SEC-D-13)": "pipeshub_agent_harness_egress_decisions_total",
    "refused tokens (SEC-D-13)": "pipeshub_agent_harness_token_rejected_total",
    "approval requests (SEC-D-13)": "pipeshub_agent_harness_approvals_requested_total",
    "operator stops (SEC-D-13)": "pipeshub_agent_harness_kill_switch",
}
SECURITY_ALERTS = {
    "PipesHubAuthFailureBurst": "pipeshub_auth_attempts_total",
    "HarnessEgressRefusalBurst": "pipeshub_agent_harness_egress_decisions_total",
    "HarnessGatewayTokenRejected": "pipeshub_agent_harness_token_rejected_total",
    "HarnessApprovalRequestFlood": "pipeshub_agent_harness_approvals_requested_total",
    "HarnessKillSwitchEngaged": "pipeshub_agent_harness_kill_switch",
}
RUNBOOKS = REPO_ROOT / "docs" / "agent-harness-v2" / "guides" / "runbooks.md"


def _rules() -> list[dict[str, Any]]:
    doc = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    return [rule for group in doc["groups"] for rule in group["rules"]]


def _dashboard_exprs() -> list[str]:
    doc = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    return [target["expr"] for panel in doc["panels"] for target in panel.get("targets", [])]


def _family(name: str) -> str:
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


@cache
def _source_text() -> str:
    parts: list[str] = []
    for root in SOURCE_ROOTS:
        parts.extend(
            path.read_text(encoding="utf-8", errors="replace")
            for path in root.rglob("*")
            if path.suffix in (".py", ".ts") and "generated" not in path.parts
        )
    return "\n".join(parts)


def _referenced() -> set[str]:
    exprs = [rule["expr"] for rule in _rules()] + _dashboard_exprs()
    return {_family(name) for expr in exprs for name in METRIC.findall(expr)}


def test_ob04_the_monitoring_files_parse() -> None:
    assert yaml.safe_load(PROMETHEUS.read_text(encoding="utf-8"))["scrape_configs"]
    assert _rules()
    doc = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    assert doc["uid"] and doc["panels"]
    for path in (MONITORING / "grafana" / "provisioning").rglob("*.yml"):
        assert yaml.safe_load(path.read_text(encoding="utf-8")), path


@pytest.mark.parametrize("name", sorted(_referenced()))
def test_ob04_every_series_an_expression_reads_is_registered_in_the_code(name: str) -> None:
    assert re.search(rf"['\"]{re.escape(name)}['\"]", _source_text()), (
        f"{name} is read by a dashboard or an alert but no service registers it"
    )


@pytest.mark.parametrize(("signal", "metric"), sorted(DASHBOARD_SIGNALS.items()))
def test_ob04_the_dashboard_shows_each_signal(signal: str, metric: str) -> None:
    assert any(metric in expr for expr in _dashboard_exprs()), f"no dashboard panel shows {signal} ({metric})"


@pytest.mark.parametrize(("alert", "metric"), sorted(REQUIRED_ALERTS.items()))
def test_ob05_an_alert_watches_each_signal(alert: str, metric: str) -> None:
    assert any(metric in rule["expr"] for rule in _rules()), f"no alert rule on {alert} ({metric})"


def test_ob05_every_alert_has_a_severity_and_says_what_happened() -> None:
    names = [rule["alert"] for rule in _rules()]
    assert len(names) == len(set(names))
    for rule in _rules():
        assert rule["labels"]["severity"] in ("warning", "critical"), rule["alert"]
        assert rule["annotations"]["summary"].strip(), rule["alert"]
        assert rule["annotations"]["description"].strip(), rule["alert"]


def test_ob04_prometheus_reads_the_merged_route_with_a_credential_file_only() -> None:
    (job,) = yaml.safe_load(PROMETHEUS.read_text(encoding="utf-8"))["scrape_configs"]
    assert job["metrics_path"] == INTERNAL_METRICS_PATH
    assert job["static_configs"] == [{"targets": ["pipeshub-ai:3002"]}]
    auth = job["authorization"]
    assert set(auth) == {"type", "credentials_file"}, "the credential is never inlined in the config"
    overlay = compose_services(OVERLAY)
    assert f"HARNESS_METRICS_CREDENTIAL_FILE={auth['credentials_file']}" in overlay["pipeshub-ai"]["environment"]


def test_ob04_monitoring_is_opt_in_and_local_only() -> None:
    base = compose_services(BASE)
    assert not {"prometheus", "grafana"} & set(base), "the base stack never starts the monitoring services"
    assert "HARNESS_METRICS_CREDENTIAL_FILE" not in json.dumps(base["pipeshub-ai"]), "only the overlay writes it"
    overlay = compose_services(OVERLAY)
    for name in ("prometheus", "grafana"):
        ports = overlay[name].get("ports") or []
        assert ports and all(str(port).startswith("127.0.0.1:") for port in ports), name
        assert overlay[name].get("mem_limit"), f"{name} runs within a memory limit"
    grafana_env = "\n".join(overlay["grafana"]["environment"])
    assert "GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:?" in grafana_env, "no default admin password"


def test_c5_05_the_cache_read_alert_fires_below_the_70_percent_floor_on_enough_runs() -> None:
    (rule,) = [r for r in _rules() if "pipeshub_agent_harness_run_cache_read_ratio" in r["expr"]]
    assert "< 0.7" in rule["expr"], "SLO #11's floor (C5.05: at least 70% of input read from the cache)"
    assert "_count" in rule["expr"], "a handful of runs is not a regression: the rule needs a minimum sample"
    assert rule.get("for"), "a dip that lasts one evaluation does not page"


def test_c20_48_availability_burn_alerts_use_the_99_9_budget_on_two_windows() -> None:
    burns = [r for r in _rules() if "pipeshub_agent_chat_turns_total" in r["expr"]]
    assert {r["labels"]["severity"] for r in burns} == {"critical", "warning"}, "a fast and a slow burn"
    for rule in burns:
        assert 'path="durable"' in rule["expr"] and 'outcome="failed"' in rule["expr"], rule["alert"]
        assert "0.001" in rule["expr"], "SLO.md's 99.9% target (error budget 0.1%)"
        assert rule["expr"].count("[") >= 2, "multi-window: a long window and a short one, so a burn that stopped clears"
    doc = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    titles = {panel.get("title") for panel in doc["panels"]}
    assert {"Availability (30 days)", "Error budget left (30 days, 99.9%)"} <= titles


def test_c20_48_session_api_burn_alerts_and_good_minutes_follow_slo_md() -> None:
    """SLO.md "Targets": a minute is bad when more than 1% of the session API's requests answer 5xx; the budget
    burns are the run-execution ones' shape over the 5xx share."""
    metric = "pipeshub_agent_harness_session_api_requests_total"
    burns = [r for r in _rules() if metric in r["expr"]]
    assert {r["labels"]["severity"] for r in burns} == {"critical", "warning"}, "a fast and a slow burn"
    for rule in burns:
        assert 'outcome="server_error"' in rule["expr"] and "0.001" in rule["expr"], rule["alert"]
        assert rule["expr"].count("[") >= 4, "two windows, each a ratio of two rates"
    (good,) = [e for e in _dashboard_exprs() if metric in e and "avg_over_time" in e]
    assert "> bool 0.01" in good and "[30d:1m]" in good, "per-minute badness over 30 days"


# --- SEC-D-13: the attack alerts, evaluated over synthetic series --------------------------------------------------
#
# No promtool on the build hosts, so the five expressions are evaluated here by a small model of PromQL that knows
# exactly their shapes: `AGG [by (labels)] (increase(METRIC{matchers}[W])) > N` and `AGG by (labels) (METRIC{...}) > N`.
# `increase` is the counter's rise inside the window (resets counted from zero), without Prometheus' extrapolation:
# the scenarios sit far enough from each threshold that the extrapolated value decides the same way.

Series = dict[tuple[tuple[str, str], ...], list[tuple[float, float]]]
_STEP_S = 30.0
_SHAPE = re.compile(
    r"^(?P<agg>sum|max)(?: by \((?P<by>[a-z_, ]+)\))? ?\((?:(?P<fn>increase)\()?(?P<metric>[a-z0-9_]+)"
    r"(?:\{(?P<matchers>[^}]*)\})?(?:\[(?P<window>\d+)m\]\))?\) > (?P<threshold>[0-9.]+)$"
)
_MATCHER = re.compile(r'([a-z_]+)(=~|=)"([^"]*)"')


def _seconds(duration: str | None) -> float:
    if not duration:
        return 0.0
    match = re.fullmatch(r"(\d+)([smh])", duration)
    assert match, duration
    return float(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]


def _evaluate(expr: str, series: Series, at: float) -> dict[tuple[str, ...], float]:
    shape = _SHAPE.match(" ".join(expr.split()))
    assert shape, f"the evaluator does not know this shape: {expr}"
    matchers = _MATCHER.findall(shape["matchers"] or "")
    by = [name.strip() for name in (shape["by"] or "").split(",") if name.strip()]
    window = float(shape["window"]) * 60 if shape["window"] else None
    groups: dict[tuple[str, ...], list[float]] = {}
    for key, points in series.items():
        labels = dict(key)
        if labels.get("__name__") != shape["metric"]:
            continue
        if not all(
            re.fullmatch(value, labels.get(name, "")) if op == "=~" else labels.get(name, "") == value
            for name, op, value in matchers
        ):
            continue
        if window is None:
            seen = [v for t, v in points if t <= at]
            if not seen:
                continue
            value = seen[-1]
        else:
            inside = [v for t, v in points if at - window <= t <= at]
            if len(inside) < 2:
                continue
            value = sum(b - a if b >= a else b for a, b in zip(inside, inside[1:], strict=False))
        groups.setdefault(tuple(labels.get(name, "") for name in by), []).append(value)
    agg = sum if shape["agg"] == "sum" else max
    threshold = float(shape["threshold"])
    return {group: agg(values) for group, values in groups.items() if agg(values) > threshold}


def _fires(rule: dict[str, Any], series: Series, until: float) -> bool:
    """Whether the rule is firing at ``until``: its condition held at every evaluation for its ``for``."""
    hold = _seconds(rule.get("for"))
    steps = int(hold // _STEP_S) + 1
    return all(_evaluate(rule["expr"], series, until - i * _STEP_S) for i in range(steps))


def _counter(name: str, per_minute: float, minutes: int = 30, start: float = 0.0, **labels: str) -> Series:
    """A counter scraped every 30 s, rising ``per_minute``, first seen at ``start``."""
    key = tuple(sorted({"__name__": name, "harness_service": "node", **labels}.items()))
    points = [(i * _STEP_S, start + per_minute * i * _STEP_S / 60) for i in range(int(minutes * 60 / _STEP_S) + 1)]
    return {key: points}


def _gauge(name: str, value: float, **labels: str) -> Series:
    key = tuple(sorted({"__name__": name, "harness_service": "operator_controls", **labels}.items()))
    return {key: [(i * _STEP_S, value) for i in range(61)]}


def _merge(*parts: Series) -> Series:
    merged: Series = {}
    for part in parts:
        merged.update(part)
    return merged


def _security_rule(name: str) -> dict[str, Any]:
    (rule,) = [r for r in _rules() if r["alert"] == name]
    return rule


AUTH = "pipeshub_auth_attempts_total"
EGRESS = "pipeshub_agent_harness_egress_decisions_total"
TOKENS = "pipeshub_agent_harness_token_rejected_total"
ASKS = "pipeshub_agent_harness_approvals_requested_total"
STOPS = "pipeshub_agent_harness_kill_switch"
END = 30 * 60.0

SCENARIOS: list[tuple[str, str, Series, bool]] = [
    ("PipesHubAuthFailureBurst", "credential stuffing: 12 wrong passwords a minute, spread over accounts",
     _counter(AUTH, 12, outcome="failure", method="password"), True),
    ("PipesHubAuthFailureBurst", "a script naming no known method",
     _counter(AUTH, 12, outcome="failure", method="other"), True),
    ("PipesHubAuthFailureBurst", "people mistyping: 2 a minute beside 40 good sign-ins",
     _merge(_counter(AUTH, 2, outcome="failure", method="password"), _counter(AUTH, 40, outcome="success", method="password")),
     False),
    ("PipesHubAuthFailureBurst", "expired browser sessions are not sign-in guesses",
     _counter(AUTH, 100, outcome="failure", method="session"), False),
    ("PipesHubAuthFailureBurst", "a users service outage is an error, not a failure",
     _counter(AUTH, 100, outcome="error", method="password"), False),
    ("HarnessEgressRefusalBurst", "an agent probing the allowlist: 5 refusals a minute",
     _counter(EGRESS, 5, decision="refused", reason="destination_not_allowed"), True),
    ("HarnessEgressRefusalBurst", "a few refusals of different reasons, and much allowed traffic",
     _merge(_counter(EGRESS, 1, decision="refused", reason="destination_not_allowed"),
            _counter(EGRESS, 1, decision="refused", reason="site_read_only"),
            _counter(EGRESS, 200, decision="allowed", reason="http")), False),
    ("HarnessGatewayTokenRejected", "one forged gateway token after a quiet start at 0",
     {tuple(sorted({"__name__": TOKENS, "kind": "gateway_token", "reason": "signature"}.items())):
      [(i * _STEP_S, 0.0 if i < 50 else 1.0) for i in range(61)]}, True),
    ("HarnessGatewayTokenRejected", "a stolen worker credential",
     _counter(TOKENS, 1, kind="machine_credential", reason="credential"), True),
    ("HarnessGatewayTokenRejected", "tokens that expired or lost their lease are not forgeries",
     _merge(_counter(TOKENS, 30, kind="gateway_token", reason="expired"),
            _counter(TOKENS, 5, kind="attempt_token", reason="superseded"),
            _counter(TOKENS, 0, kind="gateway_token", reason="signature")), False),
    ("HarnessApprovalRequestFlood", "an approval-spam run: 8 asks a minute in one org",
     _counter(ASKS, 8, org_bucket="2a"), True),
    ("HarnessApprovalRequestFlood", "a busy deployment: 2 a minute in each of ten orgs",
     _merge(*(_counter(ASKS, 2, org_bucket=f"{i:02x}") for i in range(10))), False),
    ("HarnessKillSwitchEngaged", "the instance kill switch", _gauge(STOPS, 1, scope="instance"), True),
    ("HarnessKillSwitchEngaged", "an egress stop", _gauge(STOPS, 1, scope="egress"), True),
    ("HarnessKillSwitchEngaged", "an admin stopped one conversation",
     _merge(_gauge(STOPS, 3, scope="session"), _gauge(STOPS, 0, scope="instance")), False),
]


@pytest.mark.parametrize(("alert", "metric"), sorted(SECURITY_ALERTS.items()))
def test_sec_d_13_each_attack_alert_reads_its_counter_and_links_its_runbook(alert: str, metric: str) -> None:
    rule = _security_rule(alert)
    assert metric in rule["expr"], alert
    url = rule["annotations"]["runbook_url"]
    assert url.startswith("https://github.com/pipeshub-ai/pipeshub-ai/blob/main/docs/agent-harness-v2/guides/runbooks.md#")
    anchor = url.rsplit("#", 1)[1]
    headings = {
        re.sub(r"[^a-z0-9 _-]", "", line[3:].strip().lower()).replace(" ", "-")
        for line in RUNBOOKS.read_text(encoding="utf-8").splitlines()
        if line.startswith(("## ", "### "))
    }
    assert anchor in headings, f"{alert}: no runbook section #{anchor}"


def test_sec_d_13_no_attack_alert_groups_by_an_identifying_label() -> None:
    for alert in SECURITY_ALERTS:
        expr = _security_rule(alert)["expr"]
        assert not re.search(r"by \([^)]*\b(org|org_id|orgId|user|email|token|host|run_id)\b", expr), alert


@pytest.mark.parametrize(("alert", "case", "series", "fires"), SCENARIOS, ids=[f"{a}: {c}" for a, c, _, _ in SCENARIOS])
def test_sec_d_13_the_alert_fires_on_the_attack_and_not_on_ordinary_use(
    alert: str, case: str, series: Series, fires: bool
) -> None:
    assert _fires(_security_rule(alert), series, END) is fires, case


def test_sec_d_13_every_attack_alert_has_a_firing_and_a_quiet_scenario() -> None:
    assert {(a, f) for a, _, _, f in SCENARIOS} == {(a, f) for a in SECURITY_ALERTS for f in (True, False)}
