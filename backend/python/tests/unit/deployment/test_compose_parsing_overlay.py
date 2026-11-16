"""The Compose parsing overlay shares the app's configuration store and lets a parse finish on stop."""

import pathlib

import yaml

COMPOSE = pathlib.Path(__file__).resolve().parents[5] / "deployment" / "docker-compose"


def _env(service: dict) -> dict[str, str]:
    return dict(item.split("=", 1) for item in service["environment"] if isinstance(item, str))


def _services() -> tuple[dict, dict]:
    base = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text())["services"]["pipeshub-ai"]
    parsing = yaml.safe_load((COMPOSE / "docker-compose.parsing.yml").read_text())["services"]["parsing"]
    return base, parsing


def test_parsing_reads_the_apps_redis_key_space_and_database() -> None:
    base, parsing = _services()
    for key in ("REDIS_KV_PREFIX", "REDIS_DB"):
        assert _env(parsing)[key] == _env(base)[key], key


def test_parsing_has_time_to_drain_before_the_kill() -> None:
    _, parsing = _services()
    assert "120s" in parsing["stop_grace_period"]
