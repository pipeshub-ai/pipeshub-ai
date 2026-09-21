"""`RunStore`: one directory per run, append-only JSONL plus atomic artefacts.

Appends are serialised, flushed and fsynced so a crash loses at most the item
in flight; readers tolerate a truncated final line. Every stage decides what
is left to do by diffing its work keys against what the store already holds,
which is what makes `--resume` idempotent.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Hashable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from benchmarks.frames.config import RunConfig
from benchmarks.frames.errors import ResumeMismatchError

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

CONFIG_FILE = "config.resolved.yaml"
CONFIG_HASH_FILE = "config.hash"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_jsonl(path: Path, model: type[M]) -> list[M]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    items: list[M] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            items.append(model.model_validate_json(line))
        except ValidationError:
            if number == len(lines):
                logger.warning("ignoring truncated last line of %s", path)
                continue
            raise
    return items


def new_run_id(config: RunConfig, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{config.run_name}-{config.config_hash()[:8]}"


class RunStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def run_id(self) -> str:
        return self.run_dir.name

    @classmethod
    def create(cls, root: Path, config: RunConfig) -> RunStore:
        store = cls(root / new_run_id(config))
        store.write_text(CONFIG_FILE, yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False))
        store.write_text(CONFIG_HASH_FILE, config.config_hash())
        return store

    @classmethod
    def resume(cls, root: Path, run_id: str, config: RunConfig) -> RunStore:
        store = cls(root / run_id)
        stored = store.path(CONFIG_HASH_FILE)
        if not stored.exists():
            raise ResumeMismatchError(f"{run_id} has no recorded config hash")
        if stored.read_text().strip() != config.config_hash():
            raise ResumeMismatchError(
                f"{run_id} was created from a different config; start a new run instead",
            )
        return store

    def path(self, name: str) -> Path:
        return self.run_dir / name

    def append(self, name: str, record: BaseModel) -> None:
        line = record.model_dump_json() + "\n"
        with self._lock:
            path = self.path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def read(self, name: str, model: type[M]) -> list[M]:
        return read_jsonl(self.path(name), model)

    def latest_by_key(
        self, name: str, model: type[M], key: Callable[[M], Hashable],
    ) -> dict[Hashable, M]:
        """Last record wins, so a retried item supersedes its earlier failure."""
        return {key(item): item for item in self.read(name, model)}

    def write_text(self, name: str, text: str) -> None:
        atomic_write_text(self.path(name), text)

    def write_model(self, name: str, model: BaseModel) -> None:
        self.write_text(name, model.model_dump_json(indent=2))

    def read_model(self, name: str, model: type[M]) -> M | None:
        path = self.path(name)
        return model.model_validate_json(path.read_text()) if path.exists() else None

    def write_json(self, name: str, payload: object) -> None:
        self.write_text(name, json.dumps(payload, indent=2, sort_keys=True, default=str))
