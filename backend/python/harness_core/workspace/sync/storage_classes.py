"""Storage classes (ADR-RUN-01 D2): what a workspace folder holds is the person's files, references, or scratch.

* **durable**: the person's files. Versioned in the workspace store, capped, retained and shown.
* **reference**: a fetched copy (a repository ``ph git clone`` checked out). Stored as an address and a version with
  its provenance (``STORAGE_RECORD_PATH``), never its content: it stays in the sandbox it was fetched into, and a later
  turn materialises it again from the deployment's cache under the approval it was fetched with. The mark is the
  engine's to write, so only a mark of a repository the worker itself fetched for the run (``SyncState.vouched``) is
  recorded; any other only keeps its folder out of the store.
* **scratch**: the engine's own bookkeeping (the tool results ``ph`` saves under ``notes/tool-results/``). Kept in the sandbox while the
  run works, never committed or shown, gone with the sandbox. A deliverable that cites one by path makes it durable.

A producer declares the class of what it creates, here and nowhere else: a ``PathRule`` for a kind of path, a
``MarkerRule`` for a mark it leaves in a folder (WP-82's ``pipeshub.fetched`` in a checkout's git config). The sync
asks the registry; it keeps no skip lists of its own.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, cast

from harness_core.workspace.sync.rules import (
    GIT_CONFIG,
    MAX_GIT_CONFIG_BYTES,
    fetched_checkout_of,
    marks_fetched,
    under_any,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

StorageClass = Literal["durable", "reference", "scratch"]
DURABLE: Final = "durable"
REFERENCE: Final = "reference"
SCRATCH: Final = "scratch"

# The store-only record of a revision's references. The sandbox never gets it and the engine cannot write it; Node
# lists its entries as references and never as files (``storage-record.ts``, pinned by
# ``tests/cohesion/test_wp98_storage_record.py``).
STORAGE_RECORD_DIR: Final = ".pipeshub"
STORAGE_RECORD_PATH: Final = f"{STORAGE_RECORD_DIR}/references.json"
RECORD_SCHEMA: Final = 1
# A record past these is read as holding no references (a correct worker never writes one).
MAX_RECORD_REFERENCES: Final = 64
MAX_RECORD_BYTES: Final = 64 * 1024
# WP98-FU-1 (ADR-RUN-02): where ``ph git clone`` checks a repository out, a mount of its own outside the synced
# workspace, never saved or committed. The workspace holds a link to the checkout at the path the engine asked for.
REFERENCES_DIR: Final = "/pipeshub/references"
# The folder deliverables go in (the engine controller's ``outputs_dir``): a scratch file one of them cites by path
# is the person's.
DELIVERABLES_DIR: Final = "out"
# What of a deliverable is read for citations, at most.
MAX_CITING_BYTES: Final = 4 * 1024 * 1024


def reference_identity(address: str) -> str:
    """What two addresses of one repository share: ``github.com/acme/widgets`` for any case, ``.git`` suffix or
    trailing slash."""
    rest = address.split("://", 1)[-1].strip("/").lower()
    return rest.removesuffix(".git")


def is_storage_record(path: str) -> bool:
    """Whether ``path`` is under the store-only record folder: the harness writes it, a sandbox never sees it."""
    return path == STORAGE_RECORD_DIR or path.startswith(STORAGE_RECORD_DIR + "/")


@dataclass(frozen=True, slots=True)
class Reference:
    """A fetched copy at ``path`` (a folder under the workspace root): where it came from and at which version."""

    path: str
    address: str
    version: str | None = None
    kind: str = "repository"
    ref: str | None = None
    full: bool = False

    @property
    def identity(self) -> str:
        return reference_identity(self.address)

    @property
    def source(self) -> str:
        """Its provenance id (ADR-RUN-01 D3's ``SourceRef`` key of the repository's root): ``url@commit``."""
        return f"{self.address}@{self.version}" if self.version else self.address

    @property
    def label(self) -> str:
        """``acme/widgets``: the address without its scheme and host."""
        rest = self.address.split("://", 1)[-1]
        _, _, path = rest.partition("/")
        return path.removesuffix(".git").strip("/") or rest

    def wire(self) -> dict[str, object]:
        entry: dict[str, object] = {"path": self.path, "kind": self.kind, "address": self.address, "source": self.source}
        if self.version:
            entry["version"] = self.version
        if self.ref:
            entry["ref"] = self.ref
        if self.full:
            entry["full"] = True
        return entry

    @classmethod
    def from_wire(cls, raw: object) -> Reference | None:
        if not isinstance(raw, dict):
            return None
        entry = cast("dict[str, object]", raw)
        path, address = entry.get("path"), entry.get("address")
        version, kind, ref = entry.get("version"), entry.get("kind", "repository"), entry.get("ref")
        if not isinstance(path, str) or not path or not isinstance(address, str) or not address.startswith("https://"):
            return None
        return cls(
            path,
            address,
            version if isinstance(version, str) and version else None,
            kind if isinstance(kind, str) else "repository",
            ref if isinstance(ref, str) and ref else None,
            entry.get("full") is True,
        )


@dataclass(frozen=True, slots=True)
class PathRule:
    """A producer's declaration: files under ``folder`` (with one of ``suffixes``, if any) are ``storage_class``,
    except under ``exclude``."""

    producer: str
    storage_class: StorageClass
    folder: str
    suffixes: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def matches(self, path: str) -> bool:
        if not path.startswith(self.folder.rstrip("/") + "/") or under_any(path, self.exclude):
            return False
        return not self.suffixes or path.lower().endswith(self.suffixes)


@dataclass(frozen=True, slots=True)
class MarkerRule:
    """A producer's mark: a folder holding ``file`` (at most ``max_bytes``) that ``read`` turns into a ``Reference``
    is that reference."""

    producer: str
    file: str
    max_bytes: int
    read: Callable[[str, bytes], Reference | None]
    folder_of: Callable[[str], str | None]


def _git_config_values(config: bytes) -> dict[tuple[str, str], str]:
    """``(section, key) -> value`` of a git config; a subsection is part of the section (``remote "origin"``)."""
    values: dict[tuple[str, str], str] = {}
    section = ""
    for raw in config.decode("utf-8", "replace").splitlines():
        line = raw.split("#", 1)[0].split(";", 1)[0].strip()
        if line.startswith("[") and "]" in line:
            head = line[1 : line.index("]")].strip()
            name, _, sub = head.partition(" ")
            section = f"{name.lower()} {sub.strip().strip(chr(34))}".strip()
            continue
        key, equals, value = line.partition("=")
        if equals:
            values[(section, key.strip().lower())] = value.strip()
    return values


def checkout_reference(config_path: str, config: bytes) -> Reference | None:
    """The reference ``ph git clone`` registers by its mark (``pipeshub.fetched``, WP-63/WP-82): the checkout's
    origin and the commit, ref and depth it was fetched at. A checkout made before the commit was recorded has no
    version."""
    folder = fetched_checkout_of(config_path)
    return None if folder is None else _checkout_at(folder, config)


def _checkout_at(folder: str, config: bytes) -> Reference | None:
    if not marks_fetched(config):
        return None
    values = _git_config_values(config)
    address = values.get(("pipeshub", "address")) or values.get(('remote origin', "url"), "")
    if not address.startswith("https://"):
        return None
    return Reference(
        folder,
        address,
        values.get(("pipeshub", "commit")) or None,
        "repository",
        values.get(("pipeshub", "ref")) or None,
        values.get(("pipeshub", "full"), "").lower() == "true",
    )


@dataclass(frozen=True, slots=True)
class LinkRule:
    """A producer's link (ADR-RUN-02): a workspace link whose target is a folder under ``mount`` is a reference
    checked out there. ``file`` (at most ``max_bytes``), read in that folder, is what ``read`` turns into the
    reference at the link's path. A link into ``mount`` is the producer's whatever its file says: never the person's."""

    producer: str
    mount: str
    file: str
    max_bytes: int
    read: Callable[[str, bytes], Reference | None]

    def checkout_of(self, target: str) -> str | None:
        """The checkout folder a link's ``target`` names, when it is one under the mount."""
        if not target.startswith("/"):
            return None
        folder = posixpath.normpath(target)
        mount = self.mount.rstrip("/")
        if not folder.startswith(mount + "/") or "/" in folder[len(mount) + 1 :]:
            return None
        return folder


GIT_CLONE_MARK: Final = MarkerRule("ph git clone", GIT_CONFIG, MAX_GIT_CONFIG_BYTES, checkout_reference, fetched_checkout_of)
# WP98-FU-1 (ADR-RUN-02): ``ph git clone`` checks out under the references mount and links the folder the engine asked
# for to it; the checkout's own git config (its ``pipeshub.*`` keys) says what the reference is.
GIT_CLONE_LINK: Final = LinkRule("ph git clone", REFERENCES_DIR, GIT_CONFIG, MAX_GIT_CONFIG_BYTES, _checkout_at)
# WP98-FU-4: the producer declares scratch. ``ph tools call`` saves a large result into this
# one folder itself (``harness_engines/pipeshub/ph.py`` mirrors the name) and the engine instructions name it, so the
# rule is that folder only: a person's own notes/plan.json, or a dump a model wrote anywhere else, stays durable. A
# kept dump costs a little storage; a dropped file of the person's is lost. An engine product with a native scratch
# area of its own declares it through ``StorageClassRegistry.declare``.
TOOL_RESULTS_DIR: Final = "notes/tool-results"
ENGINE_TOOL_OUTPUT: Final = PathRule("ph tools call", SCRATCH, TOOL_RESULTS_DIR)


@dataclass(frozen=True, slots=True)
class StorageMap:
    """What one scan of a folder holds, by class: the references found in it and the scratch files."""

    references: tuple[Reference, ...] = ()
    scratch: frozenset[str] = frozenset()
    # Links into a references mount (ADR-RUN-02): the producer's, kept in the sandbox and never saved, whether or not
    # the checkout behind them could be read.
    linked: frozenset[str] = frozenset()

    @property
    def reference_folders(self) -> frozenset[str]:
        return frozenset(ref.path for ref in self.references) | self.linked


@dataclass(frozen=True, slots=True)
class StorageClassRegistry:
    """The producers' declarations, and the one answer to "which class is this path"."""

    rules: tuple[PathRule, ...] = ()
    markers: tuple[MarkerRule, ...] = ()
    deliverables: str = DELIVERABLES_DIR
    links: tuple[LinkRule, ...] = ()

    @classmethod
    def default(cls) -> StorageClassRegistry:
        return cls((ENGINE_TOOL_OUTPUT,), (GIT_CLONE_MARK,), links=(GIT_CLONE_LINK,))

    def declare(self, rule: PathRule) -> StorageClassRegistry:
        """A producer's rule, after the built-in ones."""
        return StorageClassRegistry((*self.rules, rule), self.markers, self.deliverables, self.links)

    def linked(self, links: Mapping[str, str]) -> dict[str, tuple[LinkRule, str]]:
        """The scan's links (path -> target) that a link rule claims: path -> (rule, the checkout folder)."""
        found: dict[str, tuple[LinkRule, str]] = {}
        for path, target in links.items():
            for rule in self.links:
                folder = rule.checkout_of(target)
                if folder is not None:
                    found[path] = (rule, folder)
                    break
        return found

    def marker_files(self, files: Mapping[str, int]) -> list[str]:
        """The files of a scan (path -> size) a marker rule reads."""
        return sorted(
            path
            for path, size in files.items()
            if any(size <= m.max_bytes and m.folder_of(path) is not None for m in self.markers)
        )

    def reference_of(self, path: str, data: bytes) -> Reference | None:
        for marker in self.markers:
            if marker.folder_of(path) is not None and (found := marker.read(path, data)) is not None:
                return found
        return None

    def declared(self, path: str) -> StorageClass:
        """The class a path rule gives ``path``; durable when none does."""
        for rule in self.rules:
            if rule.matches(path):
                return rule.storage_class
        return DURABLE

    def is_deliverable(self, path: str) -> bool:
        return path.startswith(self.deliverables.strip("/") + "/")

    def storage_map(
        self,
        paths: Iterable[str],
        references: Iterable[Reference],
        *,
        durable: Iterable[str] = (),
        citing: Iterable[str] = (),
        linked: Iterable[str] = (),
    ) -> StorageMap:
        """The classes of a scan's ``paths``: inside a reference folder, a reference; a path a rule calls scratch is
        scratch unless the store already holds it (``durable``: the base's files stay the person's) or a deliverable
        names it (``citing``: the deliverables' texts)."""
        found = tuple(sorted({ref.path: ref for ref in references}.values(), key=lambda ref: ref.path))
        links = frozenset(linked)
        folders = [*(ref.path for ref in found), *links]
        held = frozenset(durable)
        texts = tuple(citing)
        scratch = frozenset(
            path
            for path in paths
            if path not in held
            and not under_any(path, folders)
            and self.declared(path) == SCRATCH
            and not any(path in text for text in texts)
        )
        return StorageMap(found, scratch, links)


def merged_references(earlier: Iterable[Reference], found: Iterable[Reference]) -> tuple[Reference, ...]:
    """The session's references after a scan: what the scan found replaces an earlier one at the same path (``ph git
    fetch`` moves its version); an earlier one the sandbox does not hold now stays recorded (a later turn
    materialises it again)."""
    by_path = {ref.path: ref for ref in earlier}
    by_path.update({ref.path: ref for ref in found})
    return tuple(by_path[path] for path in sorted(by_path))[:MAX_RECORD_REFERENCES]


def encode_record(references: Iterable[Reference]) -> bytes:
    entries = [ref.wire() for ref in sorted(references, key=lambda ref: ref.path)][:MAX_RECORD_REFERENCES]
    return json.dumps({"schema": RECORD_SCHEMA, "references": entries}, sort_keys=True, separators=(",", ":")).encode()


def decode_record(data: bytes) -> tuple[Reference, ...]:
    """The references a record holds; none for a record this worker cannot read."""
    if len(data) > MAX_RECORD_BYTES:
        return ()
    try:
        document = json.loads(data)
    except ValueError:
        return ()
    if not isinstance(document, dict):
        return ()
    body = cast("dict[str, object]", document)
    entries = body.get("references")
    if body.get("schema") != RECORD_SCHEMA or not isinstance(entries, list):
        return ()
    found = [ref for raw in cast("list[object]", entries)[:MAX_RECORD_REFERENCES] if (ref := Reference.from_wire(raw))]
    return tuple(sorted({ref.path: ref for ref in found}.values(), key=lambda ref: ref.path))


def reference_line(reference: Reference) -> str:
    """What an engine's brief says of a reference from an earlier turn, and how to have it again."""
    at = f" at {reference.version[:7]}" if reference.version else ""
    command = f"ph git clone {reference.address} {reference.path}"
    if reference.ref:
        command += f" --ref {reference.ref}"
    if reference.full:
        command += " --full"
    return (
        f"- {reference.path}/: {reference.label}{at}, a fetched copy (not saved with your files). If you need it and "
        f"{reference.path}/ is missing, run `{command}`: PipesHub restores it from its cache without asking the person "
        "again."
    )


__all__ = [
    "DELIVERABLES_DIR",
    "DURABLE",
    "ENGINE_TOOL_OUTPUT",
    "TOOL_RESULTS_DIR",
    "REFERENCES_DIR",
    "GIT_CLONE_LINK",
    "GIT_CLONE_MARK",
    "MAX_CITING_BYTES",
    "MAX_RECORD_BYTES",
    "MAX_RECORD_REFERENCES",
    "RECORD_SCHEMA",
    "REFERENCE",
    "SCRATCH",
    "STORAGE_RECORD_DIR",
    "STORAGE_RECORD_PATH",
    "LinkRule",
    "MarkerRule",
    "PathRule",
    "Reference",
    "StorageClass",
    "StorageClassRegistry",
    "StorageMap",
    "checkout_reference",
    "decode_record",
    "encode_record",
    "is_storage_record",
    "merged_references",
    "reference_identity",
    "reference_line",
]
