"""process_monitor.sh gives a process its own PostgreSQL login only when that process's login is set."""

import pathlib
import re
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[5] / "deployment" / "process_monitor.sh"
SHOW = 'printf "%s|%s|%s" "${PG_RUNTIME_SERVICE-}" "${PG_CORE_URL-}" "${PG_POOL_MAX-}"'


def _env_after(service: str, env: dict[str, str], show: str = SHOW) -> str:
    """The process env after use_own_pg_login, run alone (the script itself starts its monitor loop when sourced)."""
    text = SCRIPT.read_text()
    fn = re.search(r"^use_own_pg_login\(\) \{\n.*?^\}\n", text, re.S | re.M)
    hide = re.search(r"^export -n PG_CORE_QUERY_URL .*$", text, re.M)
    assert fn and hide, "use_own_pg_login or the export -n line not found in process_monitor.sh"
    out = subprocess.run(
        ["bash", "-c", f"{hide.group(0)}\n{fn.group(0)}\n(use_own_pg_login {service}; {show})"],
        env={"PATH": "/usr/bin:/bin", **env}, capture_output=True, text=True, check=True,
    )
    return out.stdout


SHARED = {"PG_CORE_URL": "postgresql://shared", "PG_POOL_MAX": "10"}


@pytest.mark.parametrize("service", ["query", "indexing", "connectors"])
def test_a_process_without_its_own_login_keeps_the_shared_settings(service: str) -> None:
    # Another process's login is set: this one must not pick it up, nor any role.
    other = {"PG_CORE_CONNECTORS_URL": "postgresql://connectors"} if service != "connectors" else {"PG_CORE_QUERY_URL": "postgresql://q"}
    assert _env_after(service, {**SHARED, **other}) == "|postgresql://shared|10"


def test_a_process_with_its_own_login_uses_it_as_its_role_with_its_pool_size() -> None:
    env = {**SHARED, "PG_CORE_QUERY_URL": "postgresql://query", "PG_QUERY_POOL_MAX": "4"}
    assert _env_after("query", env) == "query|postgresql://query|4"


def test_its_own_login_without_a_pool_size_keeps_the_shared_pool_size() -> None:
    assert _env_after("indexing", {**SHARED, "PG_CORE_INDEXING_URL": "postgresql://idx"}) == "indexing|postgresql://idx|10"


def test_a_child_inherits_only_its_own_login() -> None:
    env = {**SHARED, "PG_CORE_QUERY_URL": "postgresql://query", "PG_CORE_INDEXING_URL": "postgresql://idx"}
    # what the started process sees: env(1) lists exported variables only
    seen = _env_after("query", env, show="env | grep ^PG_CORE_ | sort")
    assert seen.split() == ["PG_CORE_URL=postgresql://query"]


def test_each_python_service_start_line_uses_it() -> None:
    text = SCRIPT.read_text()
    for service, module in (("indexing", "indexing_main"), ("connectors", "connectors_main"), ("query", "query_main")):
        assert f"(use_own_pg_login {service}; exec python -m app.{module}) &" in text
