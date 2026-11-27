"""R47-POLISH: what the e2e lane's seed and scripts give every stack.

- R46LANE-FU-2: Projects is a Labs flag that is off by default, and the projects API answers 403 while it is off,
  so the matrix rows that make a project failed on every stack. ``seed.py seed`` (run on every stack) turns on the
  flag the product reads, under the name the product reads it by.
- R46LANE-FU-4: every Playwright worker signs in once as the same admin, so the account's sign-in limit (10 a minute)
  made them wait out 429s. The lane's env file gives the setting Node's sign-in limiter reads an allowance, as it
  does ``MAX_REQUESTS_PER_MINUTE``; Compose hands it to the app with the product default for every other stack.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from typing import TYPE_CHECKING, Any

from tests.cohesion.compose_files import BASE as BASE_COMPOSE
from tests.cohesion.compose_files import compose_services
from tests.cohesion.support import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    import pytest

E2E = REPO_ROOT / "deployment" / "e2e"
NODE_SRC = REPO_ROOT / "backend" / "nodejs" / "apps" / "src"
PROJECTS_FEATURE = NODE_SRC / "modules" / "projects" / "utils" / "projects-feature.ts"
APP_CONFIG = NODE_SRC / "modules" / "tokens_manager" / "config" / "config.ts"
RUN_SH = E2E / "run.sh"
FLAG_DEFS = NODE_SRC / "modules" / "configuration_manager" / "constants" / "constants.ts"
SETTINGS = "/api/v1/configurationManager/platform/settings"
EFFECTIVE = "/api/v1/configurationManager/platform/feature-flags/effective"


def _projects_flag() -> str:
    """The flag ``requireProjectsEnabled`` gates the projects API on, read from its source."""
    found = re.findall(r"featureFlags\.([A-Z_]+) === true", PROJECTS_FEATURE.read_text(encoding="utf-8"))
    assert len(found) == 1, f"expected one flag read in {PROJECTS_FEATURE.name}, found {found}"
    return found[0]


def _load_seed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    for name, value in {"E2E_API": "http://seed.invalid", "E2E_ADMIN_EMAIL": "admin@e2e.invalid",
                        "E2E_ADMIN_PASSWORD": "unused", "E2E_LLM_KEY": "unused",
                        "E2E_STATE": str(tmp_path / "state.json")}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.syspath_prepend(str(E2E))
    spec = importlib.util.spec_from_file_location("pxh_e2e_seed_r47", E2E / "seed.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "pxh_e2e_seed_r47", module)
    spec.loader.exec_module(module)
    return module


def test_r46lane_fu2_the_projects_flag_is_off_by_default_so_the_lane_must_turn_it_on() -> None:
    flag = _projects_flag()
    text = FLAG_DEFS.read_text(encoding="utf-8")
    block = text[text.index(f"key: '{flag}'") :]
    block = block[: block.index("}")]
    assert "defaultEnabled: false" in block, f"{flag} is no longer off by default; the seed's step may go"


def test_r46lane_fu2_seed_turns_on_the_flag_the_projects_api_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    flag = _projects_flag()
    seed = _load_seed(monkeypatch, tmp_path)
    # A stack seeded before: the org, models and agent exist, so only the per-seed steps call the API.
    (tmp_path / "state.json").write_text(json.dumps(
        {"orgId": "o1", "llmKey": "llm-1", "embeddingKey": "emb-1", "agentKey": "a1"}), encoding="utf-8")
    stored: dict[str, Any] = {"featureFlags": {"ENABLE_MCP": True}, "fileUploadMaxSizeBytes": 1024}
    posted: list[dict[str, Any]] = []

    def call(method: str, path: str, body: Any = None, token: str | None = None,  # noqa: ANN401
             headers: dict[str, str] | None = None, timeout: float = 120) -> tuple[Any, dict[str, str]]:
        if path == "/api/v1/userAccount/initAuth":
            return None, {"x-session-token": "s"}
        if path == "/api/v1/userAccount/authenticate":
            return {"accessToken": "t"}, {}
        if path == "/api/v1/configurationManager/ai-models":
            return {"models": {"llm": [{"modelKey": "llm-1", "isDefault": True}]}}, {}
        if path == SETTINGS and method == "POST":
            posted.append(body)
            stored.update(json.loads(json.dumps(body)))
            return None, {}
        if path == SETTINGS:
            return json.loads(json.dumps(stored)), {}
        if path == EFFECTIVE:
            return {"featureFlags": dict(stored["featureFlags"])}, {}
        return None, {}

    monkeypatch.setattr(seed, "call", call)
    seed.seed()

    assert stored["featureFlags"].get(flag) is True, f"seed.py seed left {flag} {stored['featureFlags'].get(flag)!r}"
    assert stored["featureFlags"].get("ENABLE_MCP") is True, "turning Projects on must keep the other Labs flags"
    assert all(b.get("fileUploadMaxSizeBytes") == 1024 for b in posted), "the stored upload limit must be kept"


def _sign_in_limit_setting() -> tuple[str, int]:
    """The environment name and default behind ``maxAuthRequestsPerMinute``, read from Node's app config."""
    found = re.findall(r"maxAuthRequestsPerMinute: perMinuteLimit\(\s*'([A-Z_]+)',\s*(\d+),?\s*\)",
                       APP_CONFIG.read_text(encoding="utf-8"))
    assert len(found) == 1, f"expected one sign-in limit read in {APP_CONFIG.name}, found {found}"
    return found[0][0], int(found[0][1])


def test_r46lane_fu4_the_lane_env_gives_the_sign_in_limiter_an_allowance() -> None:
    name, default = _sign_in_limit_setting()
    assert default == 10, "the product's sign-in limit changed; the lane's allowance was sized against 10"
    values = re.findall(rf"^{name}=(\S*)$", RUN_SH.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert len(values) == 1, f"run.sh's stack env must set {name} once, found {values}"
    # Sixteen workers' sign-ins and the specs' own UI sign-ins land in the same minute.
    assert values[0].isdigit() and int(values[0]) >= 100, f"{name}={values[0]} is no allowance"


def test_r46lane_fu4_compose_hands_the_setting_to_the_app_with_the_product_default() -> None:
    name, default = _sign_in_limit_setting()
    environment = compose_services(BASE_COMPOSE)["pipeshub-ai"].get("environment") or []
    assert f"{name}=${{{name}:-{default}}}" in environment, f"the app is not given {name} from the env file"
    for overlay in (E2E / "docker-compose.e2e.yml", E2E / "docker-compose.matrix.yml"):
        for service in compose_services(overlay).values():
            entries = service.get("environment") or []
            listed = entries.items() if isinstance(entries, dict) else (e.partition("=")[::2] for e in entries)
            assert all(key != name for key, _ in listed), f"{overlay.name} pins {name} over the lane's env file"
