"""Outcome verification (G-05, GA-04): deterministic checks of what a run did, when it says it is done.

A model's own "verified" is not evidence. The run's verify gate (``RunSpec.gates.verify``) names checks the harness
runs itself, beside the answer: a file in the run's workspace exists and opens as what it claims to be, a JSON file
validates against a schema, every passage the answer cites is a source the run read and every text it quotes beside
a citation is in that source, a command (the tests) exits 0 in the run's own sandbox. Each check is a pure function of
what its port returns; a check whose port the host does not have fails closed, with a reason that says it could not
be checked, so a run is never ``verified`` by default.

The result is journaled as ``verification.recorded`` (status, round, each check's reason in plain words). With ``onFail:
continue`` a failed round is not the end: the run is told what failed and works on, up to ``maxRounds`` times.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, cast

from jsonschema import Draft202012Validator, SchemaError

from harness_core.archive_limits import (
    EXPANDED_TOO_LARGE,
    RATIO_TOO_HIGH,
    TOO_MANY_MEMBERS,
)
from harness_core.archive_reader import (
    CORRUPT,
    ENTRY_TOO_LARGE,
    ArchiveLimits,
    ArchiveRefused,
    check_zip,
    zip_declared_entries,
)
from harness_core.journal.types import NewEvent
from harness_core.workspace.types import normalize_workspace_file_path

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from jsonschema.exceptions import ValidationError
    from jsonschema.protocols import Validator
    from pydantic import JsonValue

    from harness_core.contracts.generated.run_spec import (
        CommandCheck,
        FileCheck,
        JsonSchemaCheck,
        VerifyGate,
    )
    from harness_core.workspace.ports import BinaryObjectStore, WorkspaceStore
    from harness_core.workspace.types import ObjectScope

VERIFIED_EVENT: Final = "verification.recorded"
# The round the run is on, journaled with each verification that lets it work on.
VERIFY_SLOT: Final = "gates.verify"
DEFAULT_COMMAND_TIMEOUT_S: Final = 120
MAX_REASON_CHARS: Final = 1000
# Quoted text shorter than this is a word in quotes, not a quotation of the source.
MIN_QUOTE_CHARS: Final = 12
_CITATION: Final = re.compile(r"\[([^\]]*?)\]\((ref\d+|https?://[^)\s]+)\)")
_QUOTE: Final = re.compile(r"\"([^\"]+)\"|“([^”]+)”|«([^»]+)»")
_SENTENCE_END: Final = re.compile(r"(?<=[.!?])\s+|\n+")
_ZIP_CHECK_FLOOR_BYTES: Final = 16 * 1024**2
_ZIP_CHECK_MULTIPLE: Final = 20
_ZIP_CHECK_MAX_BYTES: Final = 1024**3
_ZIP_CHECK_MAX_ENTRIES: Final = 20_000
_ZIP_PARTS: Final[dict[str, str]] = {
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
    "pptx": "ppt/presentation.xml",
}

CheckKind = Literal["file", "json_schema", "citations", "command"]


class WorkspaceFiles(Protocol):
    """The run's workspace as it stands when the run says it is done."""

    async def read(self, path: str) -> bytes | None:
        """The file's bytes; ``None`` when the workspace has no such file."""
        ...


class CitationSources(Protocol):
    """The sources the run read, by the target an answer's citation names (``refN`` or a URL)."""

    async def source_text(self, target: str) -> str | None:
        """The cited passage's text; ``None`` when the run read no source by that target."""
        ...


@dataclass(frozen=True)
class CommandOutcome:
    exit_code: int | None
    # The output's last lines, for the reason; never journaled whole.
    tail: str = ""
    timed_out: bool = False


class CommandRunner(Protocol):
    """Runs a command in the run's own sandbox, on its workspace, with no network."""

    async def run(self, argv: Sequence[str], *, timeout_s: int) -> CommandOutcome: ...


@dataclass(frozen=True)
class VerifyPorts:
    """What a host lets the verify gate look at; a check whose port is ``None`` fails closed."""

    files: WorkspaceFiles | None = None
    citations: CitationSources | None = None
    commands: CommandRunner | None = None


@dataclass(frozen=True)
class CheckResult:
    kind: CheckKind
    passed: bool
    target: str | None = None
    reason: str | None = None

    def wire(self) -> dict[str, JsonValue]:
        data: dict[str, JsonValue] = {"kind": self.kind, "passed": self.passed}
        if self.target:
            data["target"] = self.target[:512]
        if not self.passed and self.reason:
            data["reason"] = self.reason[:MAX_REASON_CHARS]
        return data


@dataclass(frozen=True)
class Verification:
    checks: tuple[CheckResult, ...]
    round: int = 1
    continuing: bool = False

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def status(self) -> Literal["verified", "unverified"]:
        return "verified" if self.passed else "unverified"

    def event(self) -> NewEvent:
        return NewEvent(
            VERIFIED_EVENT,
            {
                "status": self.status,
                "round": self.round,
                "continuing": self.continuing,
                "checks": [check.wire() for check in self.checks],
            },
        )

    def recovery(self) -> str:
        """What the run is told when it may work on: every failed check's reason."""
        failed = [f"- {check.reason}" for check in self.checks if not check.passed and check.reason]
        return (
            "Before this run counts as done, its results are checked, and these checks failed:\n"
            + "\n".join(failed)
            + "\nFix what they name, then give your final answer again. Do not claim a check passes unless it does."
        )


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = folded.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(folded.split())


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---- the checks ----


def _opens_as(data: bytes, fmt: str) -> str | None:
    """Why ``data`` does not open as ``fmt``; ``None`` when it does."""
    match fmt:
        case "json":
            try:
                json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                return f"it is not valid JSON ({type(exc).__name__})"
        case "text":
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                return "it is not UTF-8 text"
        case "csv":
            try:
                rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
            except (UnicodeDecodeError, csv.Error) as exc:
                return f"it is not a readable CSV file ({type(exc).__name__})"
            if not rows:
                return "it has no rows"
        case "pdf":
            if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
                return "it is not a complete PDF file"
        case "png":
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                return "it is not a PNG image"
        case "jpeg":
            if not data.startswith(b"\xff\xd8\xff"):
                return "it is not a JPEG image"
        case "zip" | "docx" | "xlsx" | "pptx":
            return _zip_problem(data, fmt)
        case _:
            return f"the format {fmt} is not one this gate checks"
    return None


def _zip_limits(size: int) -> ArchiveLimits:
    """What checking a zip may inflate: a small multiple of the file, never what its headers claim (SEC-C-07)."""
    budget = min(_ZIP_CHECK_MAX_BYTES, max(_ZIP_CHECK_FLOOR_BYTES, _ZIP_CHECK_MULTIPLE * size))
    return ArchiveLimits(
        max_total_bytes=budget,
        max_entry_bytes=budget,
        max_entries=_ZIP_CHECK_MAX_ENTRIES,
        ratio_floor_bytes=_ZIP_CHECK_FLOOR_BYTES,
    )


def _zip_problem(data: bytes, fmt: str) -> str | None:
    label = fmt.upper() if fmt != "zip" else "ZIP"
    if zip_declared_entries(data) is None:
        return f"it is not a {label} file"
    try:
        names = check_zip(data, _zip_limits(len(data)))
    except ArchiveRefused as exc:
        if exc.reason_code in (EXPANDED_TOO_LARGE, RATIO_TOO_HIGH, ENTRY_TOO_LARGE):
            return "it expands far beyond its size when unpacked, so it was not opened"
        if exc.reason_code == TOO_MANY_MEMBERS:
            return f"it holds more than {_ZIP_CHECK_MAX_ENTRIES} entries, so it was not opened"
        if exc.reason_code == CORRUPT:
            return "the archive is damaged"
        return f"the archive cannot be opened safely ({exc})"
    part = _ZIP_PARTS.get(fmt)
    if part is not None and part not in names:
        return f"it is not a {fmt.upper()} document"
    return None


async def check_file(check: FileCheck, files: WorkspaceFiles | None) -> CheckResult:
    path = check.path
    if files is None:
        return CheckResult("file", False, path, f"The file {path} could not be checked: this run has no workspace to read.")
    data = await files.read(path)
    if data is None:
        return CheckResult("file", False, path, f"The file {path} does not exist in the run's workspace.")
    if not data:
        return CheckResult("file", False, path, f"The file {path} is empty.")
    if check.format is not None:
        # Parsing and inflating are CPU work on the worker's loop, whose leases keep every run alive.
        problem = await asyncio.to_thread(_opens_as, data, check.format)
        if problem is not None:
            return CheckResult("file", False, path, f"The file {path} does not open as {check.format}: {problem}.")
    return CheckResult("file", True, path)


async def check_json_schema(check: JsonSchemaCheck, files: WorkspaceFiles | None) -> CheckResult:
    path = check.path
    if files is None:
        return CheckResult(
            "json_schema", False, path, f"The file {path} could not be checked: this run has no workspace to read."
        )
    data = await files.read(path)
    if data is None:
        return CheckResult("json_schema", False, path, f"The file {path} does not exist in the run's workspace.")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return CheckResult("json_schema", False, path, f"The file {path} is not valid JSON.")
    try:
        validator: Validator = Draft202012Validator(check.schema_)
        errors: list[ValidationError] = sorted(
            validator.iter_errors(cast("dict[str, Any]", document)),  # pyright: ignore[reportUnknownMemberType]
            key=lambda e: [str(p) for p in e.absolute_path],
        )
    except SchemaError:
        return CheckResult("json_schema", False, path, f"The schema for {path} is not a valid JSON Schema.")
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "the top level"
        more = f" ({len(errors) - 1} more)" if len(errors) > 1 else ""
        return CheckResult(
            "json_schema", False, path, f"The file {path} does not match its schema at {where}: {_short(first.message)}{more}."
        )
    return CheckResult("json_schema", True, path)


async def check_citations(answer: str, sources: CitationSources | None) -> CheckResult:
    """Every ``[label](target)`` names a source the run read, and every quotation in a sentence that cites is in
    one of that sentence's cited sources."""
    cited = list(_CITATION.finditer(answer))
    if not cited:
        return CheckResult("citations", True)
    if sources is None:
        return CheckResult("citations", False, None, "The answer's citations could not be checked: this run keeps no sources.")
    texts: dict[str, str | None] = {}
    for match in cited:
        target = match.group(2)
        if target not in texts:
            texts[target] = await sources.source_text(target)
    unknown = [t for t, text in texts.items() if text is None]
    if unknown:
        return CheckResult(
            "citations", False, None,
            f"The answer cites {', '.join(unknown[:5])}, which {'is' if len(unknown) == 1 else 'are'} not a source the run read.",
        )
    for sentence in _SENTENCE_END.split(answer):
        targets = [m.group(2) for m in _CITATION.finditer(sentence)]
        if not targets:
            continue
        bare = _CITATION.sub(lambda m: f" {m.group(1)} ", sentence)
        cited_text = " ".join(_norm(texts[t] or "") for t in targets)
        for quote in _QUOTE.finditer(bare):
            quoted = next(g for g in quote.groups() if g is not None)
            if len(quoted.strip()) < MIN_QUOTE_CHARS:
                continue
            if _norm(quoted.strip(" .,;:")) not in cited_text:
                return CheckResult(
                    "citations", False, None,
                    f"The answer quotes \"{_short(quoted)}\" beside {', '.join(sorted(set(targets)))}, and that text is "
                    "not in the cited source.",
                )
    return CheckResult("citations", True)


async def check_command(check: CommandCheck, commands: CommandRunner | None) -> CheckResult:
    argv = [a.root for a in check.argv]
    name = argv[0]
    if commands is None:
        return CheckResult(
            "command", False, name, f"The command {name} could not be run: this run has no sandbox to run it in."
        )
    timeout = check.timeout_s or DEFAULT_COMMAND_TIMEOUT_S
    outcome = await commands.run(argv, timeout_s=timeout)
    if outcome.timed_out:
        return CheckResult("command", False, name, f"The command {name} did not finish within {timeout} s.")
    if outcome.exit_code != 0:
        tail = f" Its output ended: {_short(outcome.tail, 400)}" if outcome.tail.strip() else ""
        code = "could not start" if outcome.exit_code is None else f"exited with code {outcome.exit_code}"
        return CheckResult("command", False, name, f"The command {name} {code}.{tail}")
    return CheckResult("command", True, name)


@dataclass
class RunVerifier:
    """The run's verify gate over the host's ports."""

    gate: VerifyGate
    ports: VerifyPorts = field(default_factory=VerifyPorts)

    @property
    def max_rounds(self) -> int:
        return self.gate.max_rounds or 1

    def may_continue(self, round_: int) -> bool:
        """A failed round ``round_`` lets the run work on (``onFail: continue``, at most ``maxRounds`` times)."""
        return self.gate.on_fail == "continue" and round_ <= self.max_rounds

    async def verify(self, answer: str, *, round_: int = 1, may_continue: bool = True) -> Verification:
        results: list[CheckResult] = []
        for check in self.gate.checks:
            match check.kind:
                case "file":
                    results.append(await check_file(check, self.ports.files))  # type: ignore[arg-type]
                case "json_schema":
                    results.append(await check_json_schema(check, self.ports.files))  # type: ignore[arg-type]
                case "citations":
                    results.append(await check_citations(answer, self.ports.citations))
                case "command":
                    results.append(await check_command(check, self.ports.commands))  # type: ignore[arg-type]
        verification = Verification(tuple(results), round_)
        if not verification.passed and may_continue and self.may_continue(round_):
            return Verification(verification.checks, round_, continuing=True)
        return verification


class StoredWorkspaceFiles:
    """``WorkspaceFiles`` over a workspace store's head manifest and its objects. ``object_scope``: where the
    objects are kept, when it is not the store's own scope (an engine run's session scope)."""

    def __init__(
        self,
        store: WorkspaceStore,
        objects: BinaryObjectStore,
        scope: ObjectScope,
        workspace_id: str,
        *,
        object_scope: ObjectScope | None = None,
    ) -> None:
        self._store = store
        self._objects = objects
        self._scope = scope
        self._object_scope = object_scope or scope
        self._workspace_id = workspace_id

    async def read(self, path: str) -> bytes | None:
        try:
            key = normalize_workspace_file_path(path)
        except ValueError:
            return None
        manifest = await self._store.head(self._scope, self._workspace_id)
        entry = manifest.files.get(key)
        if entry is None or entry.is_symlink:
            return None
        return await self._objects.get(self._object_scope, entry.content)


def verify_round(slots: Mapping[str, JsonValue]) -> int:
    """The round the next verification is: one past the last one that let the run work on."""
    value = slots.get(VERIFY_SLOT)
    return (value if isinstance(value, int) and not isinstance(value, bool) else 0) + 1


__all__ = [
    "DEFAULT_COMMAND_TIMEOUT_S",
    "VERIFIED_EVENT",
    "VERIFY_SLOT",
    "CheckResult",
    "CitationSources",
    "CommandOutcome",
    "CommandRunner",
    "RunVerifier",
    "StoredWorkspaceFiles",
    "Verification",
    "VerifyPorts",
    "WorkspaceFiles",
    "check_citations",
    "check_command",
    "check_file",
    "check_json_schema",
    "verify_round",
]
