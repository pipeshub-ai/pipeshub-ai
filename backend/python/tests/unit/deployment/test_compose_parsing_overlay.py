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
    for key in ("REDIS_KV_PREFIX", "REDIS_DB", "PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "AWS_DEFAULT_REGION", "OLLAMA_API_URL"):
        assert _env(parsing)[key] == _env(base)[key], key


def test_parsing_has_time_to_drain_before_the_kill() -> None:
    _, parsing = _services()
    assert "120s" in parsing["stop_grace_period"]


def test_parsing_is_healthy_only_when_docling_answers() -> None:
    _, parsing = _services()
    assert parsing["healthcheck"]["test"][-1].endswith("/health/ready")


def test_parsing_gets_every_redis_connection_setting_the_app_has() -> None:
    """Parsing reads the app's configuration store, so it needs the app's whole Redis setup (mode, cluster, TLS, user)."""
    base, parsing = _services()
    wanted = {key for key in _env(base) if key.startswith("REDIS_") and not key.startswith("REDIS_STREAMS_")}
    assert wanted <= set(_env(parsing)), sorted(wanted - set(_env(parsing)))
    for key in wanted:
        assert _env(parsing)[key] == _env(base)[key], key


def test_the_concurrency_settings_reach_the_containers_that_read_them() -> None:
    """Compose does not pass `.env` values into a container: each setting has to be listed."""
    overlay = yaml.safe_load((COMPOSE / "docker-compose.parsing.yml").read_text())["services"]
    assert "PARSING_SERVICE_CONCURRENCY" in _env(overlay["pipeshub-ai"])
    assert "PARSING_MAX_CONCURRENT" in _env(overlay["parsing"])


def test_parsing_resolves_host_docker_internal_like_the_app() -> None:
    base, parsing = _services()
    assert "host.docker.internal:host-gateway" in base["extra_hosts"]
    assert "host.docker.internal:host-gateway" in parsing["extra_hosts"]
