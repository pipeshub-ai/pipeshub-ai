"""OPS1-3: every stateful store a manifest runs is given an explicit open-file limit at or above its floor.

A container inherits the runtime's limit, 1024 soft on a current Docker or containerd under systemd. mongod and
Qdrant keep the soft limit they are started with, and the others raise theirs only as far as the hard limit the
host happens to have (measured on a current Docker host). Static, no Docker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.cohesion.compose_files import (
    COMPOSE_DIR,
    compose_services,
    effective_service,
    render_defaults,
)
from tests.cohesion.support import REPO_ROOT
from tests.edition_overlay import extended

HELM_DIR = REPO_ROOT / "deployment" / "helm" / "pipeshub-ai"
FAULT_STACK = REPO_ROOT / "integration-tests" / "harness" / "fault" / "docker-compose.yml"


@dataclass(frozen=True)
class Store:
    name: str
    images: tuple[str, ...]
    floor: int


# The one table: an image runs a stateful store, with the lowest limit its vendor accepts, or it is stateless.
STORES = (
    Store("mongodb", ("mongo",), 64000),
    Store("redis", ("redis", "grokzen/redis-cluster"), 10032),
    Store("neo4j", ("neo4j",), 40000),
    Store("arangodb", ("arangodb",), 8192),
    Store("qdrant", ("qdrant/qdrant",), 10000),
    Store("kafka", ("confluentinc/cp-kafka", "apache/kafka"), 100000),
    Store("zookeeper", ("confluentinc/cp-zookeeper",), 8192),
    Store("etcd", ("quay.io/coreos/etcd",), 40000),
    Store("opensearch", ("opensearchproject/opensearch",), 65536),
    # Main's PostgreSQL image runs the server and PgBouncer (#5274). PgBouncer takes a descriptor per client
    # (max_client_conn 1000) and per server connection; each server backend holds up to max_files_per_process (1000).
    Store("postgresql", ("pipeshubai/postgres",), 4096),
    # The optional monitoring overlay (C9.04): Prometheus' TSDB holds a file per block and series chunk, Grafana its
    # SQLite database and plugins.
    Store("prometheus", ("prom/prometheus",), 65536),
    Store("grafana", ("grafana/grafana",), 8192),
)
STATELESS_IMAGES = frozenset(
    {
        "pipeshubai/pipeshub-ai",
        "pipeshub-ai",
        "pipeshubai/pipeshub-sandbox",
        "pipeshub/sandbox",
        "opensearchproject/opensearch-dashboards",
        # Main's web readers (#4300): no volumes, nothing stored.
        "nginx",
        "searxng/searxng",
        "unclecode/crawl4ai",
        # Main's schema migration job (#5274): runs once against PostgreSQL, keeps nothing.
        "pipeshubai/pipeshub-migrate",
    }
)
STORE_OF_IMAGE = {image: store for store in STORES for image in store.images}
# An edition overlay's own images that store nothing (``tests/edition_overlay.py``, ``stateful_file_limits.json``).
EXTRA_STATELESS = frozenset(extended({}, "stateful_file_limits", "stateless_images"))


def _shipped(path: Path) -> bool:
    return not path.name.startswith("docker-compose.integration.")


SHIPPED = sorted(
    p for p in [*COMPOSE_DIR.glob("*.yml"), *COMPOSE_DIR.glob("beta/*.yml"), *COMPOSE_DIR.glob("hot-reload/*.yml")]
    if _shipped(p)
)
TEST_STACKS = sorted(
    [
        *(p for p in COMPOSE_DIR.glob("*.yml") if not _shipped(p)),
        *COMPOSE_DIR.glob("tests/*.yml"),
        *(REPO_ROOT / "deployment" / "e2e").glob("*.yml"),
        FAULT_STACK,
    ]
)


def repository(image: str) -> str:
    name = render_defaults(image).split("@", 1)[0]
    head, _, tail = name.rpartition("/")
    return f"{head}/{tail.split(':', 1)[0]}" if head else tail.split(":", 1)[0]


def _services(files: list[Path]) -> list[tuple[Path, str]]:
    return [(path, name) for path in files for name in compose_services(path)]


def _id(value: object) -> str:
    return str(value.relative_to(REPO_ROOT)) if isinstance(value, Path) else str(value)


def limit_problem(service: dict[str, Any], store: Store) -> str | None:
    nofile = (service.get("ulimits") or {}).get("nofile")
    if nofile is None:
        return f"sets no ulimits.nofile ({store.name} needs {store.floor})"
    soft, hard = (nofile, nofile) if isinstance(nofile, int) else (nofile.get("soft"), nofile.get("hard"))
    if not isinstance(soft, int) or not isinstance(hard, int):
        return f"ulimits.nofile {nofile!r} is not a number for both soft and hard"
    if min(soft, hard) < store.floor:
        return f"ulimits.nofile {soft}:{hard} is below the {store.floor} {store.name} needs"
    return None


def stateful_problem(path: Path, name: str) -> str | None:
    service = effective_service(path, name)
    image = service.get("image")
    store = STORE_OF_IMAGE.get(repository(image)) if isinstance(image, str) else None
    return None if store is None else limit_problem(service, store)


@pytest.mark.parametrize(("path", "name"), _services(SHIPPED + TEST_STACKS), ids=_id)
def test_ops1_3_1_a_stateful_store_sets_its_open_file_limit(path: Path, name: str) -> None:
    problem = stateful_problem(path, name)
    assert problem is None, f"{_id(path)}: service {name} {problem}"


@pytest.mark.parametrize(("path", "name"), _services(SHIPPED), ids=_id)
def test_ops1_3_3_a_shipped_service_is_classified(path: Path, name: str) -> None:
    image = effective_service(path, name).get("image")
    assert isinstance(image, str), f"{_id(path)}: service {name} has no image to classify it by"
    repo = repository(image)
    assert repo in STORE_OF_IMAGE or repo in STATELESS_IMAGES or repo in EXTRA_STATELESS, (
        f"{_id(path)}: service {name} runs {repo}, which is neither a stateful store nor declared stateless: "
        "add it to STORES with its vendor's open-file floor, or to STATELESS_IMAGES"
    )


def test_ops1_3_8_every_table_entry_is_used() -> None:
    used = {
        repository(image)
        for path, name in _services(SHIPPED + TEST_STACKS)
        if isinstance(image := effective_service(path, name).get("image"), str)
    }
    stale = sorted((set(STORE_OF_IMAGE) | STATELESS_IMAGES) - used)
    assert stale == [], f"no manifest runs {stale}: remove them from the table"


@pytest.mark.parametrize(
    ("nofile", "expected"),
    [
        (None, "sets no"),
        ({"soft": 1024, "hard": 64000}, "below"),
        ({"soft": 64000, "hard": 1024}, "below"),
        ({"soft": 64000}, "not a number"),
        (1024, "below"),
        ({"soft": 64000, "hard": 64000}, None),
        (64000, None),
    ],
)
def test_ops1_3_5_a_limit_below_the_floor_is_a_problem(nofile: object, expected: str | None) -> None:
    service = {} if nofile is None else {"ulimits": {"nofile": nofile}}
    problem = limit_problem(service, STORES[0])
    assert (problem is None) if expected is None else (problem is not None and expected in problem)


def test_ops1_3_6_an_image_is_classified_by_its_repository() -> None:
    assert repository("mongo:${MONGO_IMAGE_TAG:-8.0.17}") == "mongo"
    assert repository("quay.io/coreos/etcd:v3.5.17") == "quay.io/coreos/etcd"
    assert repository("localhost:5000/mongo") == "localhost:5000/mongo"
    assert repository("redis:7@sha256:abc") == "redis"
    assert repository("${SANDBOX_DOCKER_IMAGE:-pipeshubai/pipeshub-sandbox:${IMAGE_TAG:-latest}}") == (
        "pipeshubai/pipeshub-sandbox"
    )


# The chart's own containers for the two stores that keep the soft limit they are started with.
HELM_RAISED = (
    ("mongodb", "mongodb-statefulset.yaml", "mongodb.builtin.openFiles", r"exec (docker-entrypoint\.sh )?mongod\b"),
    ("qdrant", "qdrant-statefulset.yaml", "qdrant.openFiles", r"exec \./(qdrant|entrypoint\.sh)\b"),
)


@pytest.mark.parametrize(("store", "template", "value", "server"), HELM_RAISED)
def test_ops1_3_7_the_chart_raises_the_soft_limit_before_the_server_starts(
    store: str, template: str, value: str, server: str
) -> None:
    floor = next(s.floor for s in STORES if s.name == store)
    node: Any = yaml.safe_load((HELM_DIR / "values.yaml").read_text(encoding="utf-8"))
    for part in value.split("."):
        node = node[part]
    assert isinstance(node, int) and node >= floor, f"{value} is {node!r}; {store} needs {floor}"
    text = (HELM_DIR / "templates" / template).read_text(encoding="utf-8")
    raised = f'include "pipeshub-ai.raiseOpenFiles" .Values.{value}'
    starts = [m.start() for m in re.finditer(server, text)]
    assert starts, f"{template} no longer starts {store} from a command this case can read"
    for at in starts:
        command = text.rindex("command:", 0, at)
        assert raised in text[command:at], f"{template} starts {store} before it raises the open-file limit"
    helper = (HELM_DIR / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    body = helper[helper.index('define "pipeshub-ai.raiseOpenFiles"') :]
    body = body[: body.index("{{- end")]
    assert "ulimit -Hn" in body and "ulimit -Sn" in body, "the helper must raise the soft limit within the hard one"
