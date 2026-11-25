"""The live variant (04 El-release): the row's model is a real Azure OpenAI deployment, behind an explicit opt-in.

Off unless ``--live`` is given, and then only with a hard spend cap of at most ``MAX_CAP_USD``. The spend is the turns' journaled token usage priced at the deployment's rates
(``QA6_LIVE_USD_PER_MTOK_IN`` / ``_OUT``, no default: a run without prices does not start); once it reaches the cap
the session stops before the next row. A live model does not follow the scripts' directives, so a live row asserts
what does not depend on them: the route and its reason, the executor, a terminal answer, the journal's history.

    python -m pytest --live --live-cap-usd 7 -k "azure_openai and none"   # credentials from integration-tests/.env.local
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
MAX_CAP_USD = 7.0
ENV_FILE = REPO / "integration-tests/.env.local"
KEYS = ("TEST_AZURE_OPENAI_ENDPOINT", "TEST_AZURE_OPENAI_API_KEY", "TEST_AZURE_OPENAI_DEPLOYMENT_NAME",
        "TEST_AZURE_OPENAI_MODEL")


class LiveRefused(RuntimeError):
    """The live variant cannot start as asked (no cap, a cap above the budget, no prices, no credentials)."""


@dataclass
class Spend:
    cap_usd: float
    usd_per_mtok_in: float
    usd_per_mtok_out: float
    spent_usd: float = 0.0
    rows: list[tuple[str, float]] = field(default_factory=list[tuple[str, float]])

    def charge(self, row_id: str, events: list[dict[str, Any]]) -> float:
        tokens_in = tokens_out = 0
        for event in events:
            if event.get("type") != "model.response":
                continue
            usage = (event.get("data") or {}).get("usage") or {}
            tokens_in += int(usage.get("inputTokens") or usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("outputTokens") or usage.get("output_tokens") or 0)
        usd = tokens_in / 1e6 * self.usd_per_mtok_in + tokens_out / 1e6 * self.usd_per_mtok_out
        self.spent_usd += usd
        self.rows.append((row_id, usd))
        return usd

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self.cap_usd


def spend_from(cap_usd: float, environ: dict[str, str] | None = None) -> Spend:
    env = os.environ if environ is None else environ
    if not 0 < cap_usd <= MAX_CAP_USD:
        raise LiveRefused(f"--live-cap-usd must be above 0 and at most {MAX_CAP_USD} (the hard cap)")
    try:
        return Spend(cap_usd, float(env["QA6_LIVE_USD_PER_MTOK_IN"]), float(env["QA6_LIVE_USD_PER_MTOK_OUT"]))
    except (KeyError, ValueError):
        raise LiveRefused("set QA6_LIVE_USD_PER_MTOK_IN and QA6_LIVE_USD_PER_MTOK_OUT to the deployment's prices") from None


def credentials(path: Path = ENV_FILE) -> dict[str, str]:
    """The Azure deployment from the integration tests' local env file (``TEST_AZURE_OPENAI_*``); never logged."""
    if not path.exists():
        raise LiveRefused(f"no {path.name}: the live variant reads the TEST_AZURE_OPENAI_* settings from it")
    found = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))
    missing = [k for k in KEYS if not found.get(k, "").strip()]
    if missing:
        raise LiveRefused(f"{path.name} lacks {missing}")
    return {k: found[k].strip().strip('"') for k in KEYS}


def live_model_config(creds: dict[str, str]) -> tuple[str, dict[str, str]]:
    """The provider configuration ``Fixtures.model`` adds instead of the scripted model."""
    return "azureOpenAI", {"endpoint": creds["TEST_AZURE_OPENAI_ENDPOINT"], "apiKey": creds["TEST_AZURE_OPENAI_API_KEY"],
                           "deploymentName": creds["TEST_AZURE_OPENAI_DEPLOYMENT_NAME"],
                           "model": creds["TEST_AZURE_OPENAI_MODEL"]}


@dataclass
class LiveRun:
    spend: Spend
    creds: dict[str, str]

    @staticmethod
    def takes(factors: dict[str, str]) -> bool:
        """Rows the live variant runs: the Azure wire on the loop (an engine's model goes through its gateway), with
        no role model (a live model does not follow the script that delegates, and the lane has one live model)."""
        return factors["wire"] == "azure_openai" and factors["engine"] == "none" and factors["child_model"] == "none"
