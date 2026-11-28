#!/usr/bin/env python3
"""Print the environment a simulator shard runs with, as KEY=VALUE lines for $GITHUB_ENV.

The shards integration-tests/sim_tiers.json names (and a manual run of one of its suites) hold only simulator
suites, which check what a connector syncs and who may read it. They run on the fake model
(docker-compose.integration.fake-model.yml) instead of a paid provider, and there a suite's missing setting is an
error, not a skip: nothing a simulator suite needs is a secret, so a skip can only mean the shard is wired wrong.
Every other shard gets nothing and keeps the workflow's own settings.

The workflow runs this before the AI model check, so the check sees the provider the shard will use.

Run: python3 scripts/sim_shard_env.py --shard "$SHARD" --connectors "$CONNECTORS_INPUT" >> "$GITHUB_ENV"
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TIERS_MODULE = REPO / "integration-tests" / "helper" / "sim_tiers.py"

FAKE_MODEL_ENV = (
    ("TEST_AI_MODEL_PROVIDER", "ollama"),
    ("TEST_OLLAMA_ENDPOINT", "http://fakellm:11434"),
    ("TEST_OLLAMA_LLM_MODEL", "qwen2.5:0.5b"),
    ("TEST_OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"),
    ("PIPESHUB_REQUIRE_CONNECTOR_SECRETS", "1"),
    ("FAKE_MODEL_COMPOSE", "-f docker-compose.integration.fake-model.yml"),
    # The test steps print each suite's selection first on a simulator job.
    ("SIM_JOB", "1"),
    # A manual run of one simulator suite runs that suite: the browser specs need a real model, which it does not have.
    ("RUN_PLAYWRIGHT", "false"),
)


def _tiers_module():
    spec = importlib.util.spec_from_file_location("sim_tiers", TIERS_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def is_sim_job(shard: str, connectors: str, tiers_path: Path | None = None) -> bool:
    module = _tiers_module()
    tiers = module.load(tiers_path) if tiers_path else module.load()
    if shard in tiers.sim_shards:
        return True
    return shard == "dispatch" and connectors.strip() in tiers.suites


def env_lines(shard: str, connectors: str, tiers_path: Path | None = None) -> list[str]:
    if not is_sim_job(shard, connectors, tiers_path):
        return []
    return [f"{key}={value}" for key, value in FAKE_MODEL_ENV]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shard", required=True)
    parser.add_argument("--connectors", default="")
    parser.add_argument("--tiers", type=Path, default=None, help="sim_tiers.json to read (default: the repository's)")
    args = parser.parse_args(argv)
    for line in env_lines(args.shard, args.connectors, args.tiers):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
