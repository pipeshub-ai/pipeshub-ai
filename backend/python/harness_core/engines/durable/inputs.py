"""Conversation artifacts as a durable engine run's input files (E1b, EV2-26).

The run's input names artifacts (``Claim.input_artifacts``); an ``EngineInputSource`` resolves them as the
run's actor at the first turn's start; the kernel pins what it got in that turn's ``turn.started``
(``inputs``) and the controller seeds them under ``in/``. A re-driven turn seeds the pinned bytes again,
never resolving anew. ``StoreInputSource`` is the reference source over any ``ArtifactStore`` whose
authority is its scope (in-memory, a Loom host's own store); a host with per-user read access binds its own.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from harness_core.artifacts.errors import ArtifactPublishRefused
from harness_core.artifacts.types import ArtifactVersionRef
from harness_core.engines.errors import (
    EngineInputConflict,
    EngineInputTooLarge,
    EngineInputUnavailable,
)
from harness_core.engines.types import EngineInput
from harness_core.workspace.safe_names import check_name
from harness_core.workspace.types import ObjectRef, normalize_workspace_file_path

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.artifacts.ports import ArtifactStore
    from harness_core.engines.durable.ports import EngineInputSource
    from harness_core.interactions.ports import RunBinding
    from harness_core.journal.types import InputArtifactRef
    from harness_core.workspace.ports import BinaryObjectStore
    from harness_core.workspace.types import ObjectScope


# Names the engine's shell and tools take literally: letters and digits of any script, and these.
_SAFE_PUNCTUATION = frozenset("._-+")
# Device names Windows reserves whatever the extension; a workspace synced to a Windows machine cannot hold them.
_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
)
MAX_INPUT_NAME_BYTES = 120
_FALLBACK_NAME = "input"


def _safe_char(char: str) -> str:
    # Letters and decimal digits only: ``isalnum`` also takes superscripts, and Windows reads ``COM¹`` as a device.
    keep = unicodedata.category(char)[0] == "L" or unicodedata.category(char) == "Nd"
    return char if char in _SAFE_PUNCTUATION or (keep and char.isprintable()) else "_"


def _truncated(name: str, original: str) -> str:
    """A name over the limit keeps its extension and gains a digest of the original, so two long names that
    share a prefix stay two files."""
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem or len(extension.encode()) > 16:
        stem, extension = name, ""
    suffix = "-" + hashlib.sha256(original.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    tail = f"{suffix}.{extension}" if extension else suffix
    budget = MAX_INPUT_NAME_BYTES - len(tail.encode())
    kept = stem.encode()[:budget].decode("utf-8", "ignore")
    return kept + tail


def input_name(name: str) -> str:
    """The file an artifact becomes under ``in/`` (SEC-PI-07): the last segment of its name, reduced to
    characters no shell or tool treats as syntax.

    A person names an upload anything (``$(rm -rf ~).pdf``); the engine passes it to a shell. Every other
    character becomes ``_``, a leading ``.``/``-``/``_`` is dropped (no hidden file, no option), a reserved
    device name is prefixed, and a long name is cut with a digest of the original. The same name always
    stages as the same file."""
    base = unicodedata.normalize("NFC", name).replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"_{2,}", "_", "".join(_safe_char(c) for c in base))
    cleaned = cleaned.lstrip("._-").rstrip(".")
    if not cleaned or cleaned == "_":
        cleaned = _FALLBACK_NAME
    if cleaned.split(".", 1)[0].lower() in _RESERVED_STEMS:
        cleaned = f"file_{cleaned}"
    if len(cleaned.encode()) > MAX_INPUT_NAME_BYTES:
        cleaned = _truncated(cleaned, name)
    # SEC-FS-02: what this makes is a name the shared check takes, whatever it was given.
    return check_name(normalize_workspace_file_path(cleaned))


def distinct_inputs(inputs: Iterable[EngineInput]) -> tuple[EngineInput, ...]:
    """One input per artifact (the first ref wins); two artifacts on one file name conflict."""
    by_artifact: dict[str, EngineInput] = {}
    by_name: dict[str, str] = {}
    for item in inputs:
        artifact = item.source.artifact_id
        if artifact in by_artifact:
            continue
        if by_name.get(item.name, artifact) != artifact:
            raise EngineInputConflict("two input artifacts have the same file name")
        by_artifact[artifact] = item
        by_name[item.name] = artifact
    return tuple(by_artifact.values())


def pin_data(item: EngineInput) -> dict[str, JsonValue]:
    """An input as ``turn.started.inputs`` pins it."""
    return {
        "name": item.name,
        "artifactId": item.source.artifact_id,
        "version": item.source.version,
        "objectId": item.content.object_id,
        "contentHash": item.content.content_hash,
        "sizeBytes": item.content.size_bytes,
        "mimeType": item.mime_type,
    }


def pinned_input(data: Mapping[str, JsonValue]) -> EngineInput:
    """The input a ``turn.started`` pin names."""
    return EngineInput(
        name=str(data["name"]),
        content=ObjectRef(str(data["objectId"]), str(data["contentHash"]), int(cast("int", data["sizeBytes"]))),
        mime_type=str(data["mimeType"]),
        source=ArtifactVersionRef(str(data["artifactId"]), int(cast("int", data["version"]))),
    )


@dataclass(frozen=True, slots=True)
class SkippedInput:
    """An input artifact the run itself refused (R36COVER-FU-3): over the per-input cap, or no longer available since
    the parent handed it over. The run goes on without it; the engine is told in its first task and the person in the
    answer. ``name``: the file's name when the refusal may say it; ``words``: why, in plain words."""

    artifact_id: str
    reason: Literal["too_large", "unavailable"]
    words: str
    name: str | None = None


_GONE_WORDS = "it is no longer available to the task (deleted, or no longer shared with you)"


def skipped_of(artifact_id: str, refused: EngineInputTooLarge | EngineInputUnavailable) -> SkippedInput:
    """The skipped input a refusal of one artifact stands for."""
    if isinstance(refused, EngineInputTooLarge):
        return SkippedInput(artifact_id, "too_large", refused.why(), refused.name)
    return SkippedInput(artifact_id, "unavailable", _GONE_WORDS, refused.name)


def skip_data(item: SkippedInput) -> dict[str, JsonValue]:
    """A skipped input as ``turn.started.skippedInputs`` pins it."""
    data: dict[str, JsonValue] = {"artifactId": item.artifact_id, "reason": item.reason, "words": item.words[:300]}
    if item.name:
        data["name"] = item.name[:256]
    return data


def skipped_input(data: Mapping[str, JsonValue]) -> SkippedInput:
    """The skipped input a ``turn.started`` pin names."""
    name = data.get("name")
    reason = "too_large" if data.get("reason") == "too_large" else "unavailable"
    return SkippedInput(str(data["artifactId"]), reason, str(data.get("words") or _GONE_WORDS),
                        name if isinstance(name, str) and name else None)


async def resolve_each(
    source: EngineInputSource, binding: RunBinding, refs: Sequence[InputArtifactRef]
) -> tuple[tuple[EngineInput, ...], tuple[SkippedInput, ...]]:
    """The inputs ``refs`` name, one artifact at a time, and those refused on their own (too large, gone): a job goes
    on without a file it cannot take rather than failing whole. Any other refusal (two inputs on one name, a source
    that resolves none) still propagates."""
    inputs: list[EngineInput] = []
    skipped: list[SkippedInput] = []
    for ref in refs:
        try:
            inputs.extend(await source.resolve(binding, [ref]))
        except (EngineInputTooLarge, EngineInputUnavailable) as refused:
            if all(s.artifact_id != ref.artifact_id for s in skipped):
                skipped.append(skipped_of(ref.artifact_id, refused))
    return distinct_inputs(inputs), tuple(skipped)


# What one input may weigh by default (J-26): the release's, the same as an artifact store's default cap, so any
# artifact it holds fits. A host binds its own (PipesHub: HARNESS_INPUT_MAX_MB, lowered by sandbox.inputMaxMb).
MAX_INPUT_BYTES = 100 * 1024 * 1024


def check_input_size(name: str, size_bytes: int, max_bytes: int) -> None:
    """``EngineInputTooLarge`` for an input over ``max_bytes``; called before its bytes are read when the
    size is known, and on the bytes read before any is staged."""
    if size_bytes > max_bytes:
        raise EngineInputTooLarge(name, size_bytes, max_bytes)


# What fired a scheduled run (TR-03), as the file an engine finds at ``in/trigger.json`` (/pipeshub/inputs).
TRIGGER_INPUT_NAME = "trigger.json"
TRIGGER_INPUT_MIME = "application/json"


def trigger_input_bytes(context: Mapping[str, JsonValue]) -> bytes:
    """The trigger file's bytes: canonical JSON (sorted keys, two-space indent, a trailing newline), so the same
    firing always stages the same file."""
    return (json.dumps(context, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


async def trigger_input(
    objects: BinaryObjectStore, scope: ObjectScope, run_id: str, context: Mapping[str, JsonValue]
) -> EngineInput:
    """The trigger context as an input pinned with the first turn. It is no artifact: its source is a per-run id
    nothing resolves in the registry (the pin keeps the bytes), so a re-driven turn seeds exactly these bytes."""
    stored = await objects.put(scope, trigger_input_bytes(context))
    return EngineInput(TRIGGER_INPUT_NAME, stored, TRIGGER_INPUT_MIME, ArtifactVersionRef(f"trigger:{run_id}", 1))


def _everything(artifact_id: str) -> bool:
    del artifact_id
    return True


class StoreInputSource:
    """``EngineInputSource`` over an ``ArtifactStore`` of the run's session; ``readable`` narrows it to what
    the run's actor may read (default: everything the scope holds)."""

    def __init__(
        self,
        store: ArtifactStore,
        objects: BinaryObjectStore,
        *,
        object_scope: Callable[[RunBinding], ObjectScope],
        readable: Callable[[str], bool] | None = None,
        max_input_bytes: int = MAX_INPUT_BYTES,
    ) -> None:
        self._store = store
        self._max_bytes = max_input_bytes
        self._objects = objects
        self._object_scope = object_scope
        self._readable: Callable[[str], bool] = readable or _everything

    async def resolve(self, binding: RunBinding, refs: Sequence[InputArtifactRef]) -> tuple[EngineInput, ...]:
        inputs: list[EngineInput] = []
        for ref in refs:
            if not self._readable(ref.artifact_id):
                raise EngineInputUnavailable("an input artifact is not available to this run")
            try:
                current = await self._store.current(binding, ref.artifact_id)
                version = ref.version if ref.version is not None else current.version
                if not 1 <= version <= current.version:
                    raise EngineInputUnavailable("an input artifact has no such version")
                name = input_name(current.name)
                if version == current.version:
                    check_input_size(name, current.size_bytes, self._max_bytes)
                content = await self._store.content(binding, ref.artifact_id, version)
            except ArtifactPublishRefused:
                raise EngineInputUnavailable("an input artifact is not available to this run") from None
            check_input_size(name, len(content), self._max_bytes)
            stored = await self._objects.put(self._object_scope(binding), content)
            inputs.append(
                EngineInput(name, stored, current.mime_type, ArtifactVersionRef(current.artifact_id, version))
            )
        return distinct_inputs(inputs)


__all__ = [
    "MAX_INPUT_BYTES",
    "MAX_INPUT_NAME_BYTES",
    "SkippedInput",
    "TRIGGER_INPUT_MIME",
    "TRIGGER_INPUT_NAME",
    "StoreInputSource",
    "check_input_size",
    "distinct_inputs",
    "input_name",
    "pin_data",
    "pinned_input",
    "resolve_each",
    "skip_data",
    "skipped_input",
    "skipped_of",
    "trigger_input",
    "trigger_input_bytes",
]
