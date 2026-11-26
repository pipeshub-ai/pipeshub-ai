"""ADR-RUN-01 D7: the research-run scenario, one definition for both lanes (RUN01-SC-*).

A handful of real research runs found about fifteen harness defects between them. This is their shape, played by
the scripted model so that nothing is spent: a deep-research hand-off whose engine reads about
150 public pages and searches, clones two repositories of about 10,000 files each between them, writes a 140 KB
report in appends to ``out/report.md`` with model steps of minutes on a virtual clock, and is cut by its working
time in the middle of its last step. The lanes run it their own way (``test_run01_research_scenario.py`` in process,
``test_run01_research_scenario_docker.py`` on the real engine lane); what the shape is, what the person must see and
how it is read from the journal live here only.

Times are a recorded run's: gathering steps of a few seconds, appends of 109-226 s, a last step of 318 s that the
30-minute limit interrupted. ``outcome_facts`` reads D1's ledger and settled outcome as WP-95 journals them.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, override

from harness_core.repos.fetch import RepositoryFetcher
from tests.support.git_host import GitHttpHost, https_pki

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path

REQUEST = (
    "Do a deep research comparing Onyx (onyx.app, github.com/onyx-dot-app/onyx) and PipesHub (pipeshub.com, "
    "github.com/pipeshub-ai/pipeshub-ai). Check the docs, the GitHub code repo and the website of both. Cite every "
    "finding with its source URL. Write the final report as one Markdown file named report.md in your output folder."
)
REPORT = "out/report.md"
REPORT_NAME = "report.md"
GIT_HOST = "git.pxh-run01.dev"
REPOSITORIES = ("onyx-dot-app/onyx", "pipeshub-ai/pipeshub-ai")
# The web tools as PipesHub's engines are granted them (the tool gateway's names, the web provider's ids).
FETCH_URL, WEB_SEARCH = "dynamic__fetch_url", "dynamic__web_search"
FETCH_URL_ID, WEB_SEARCH_ID = "web@searxng/fetch_url", "web@searxng/web_search"
# A recorded run: 23 hosts asked one card each once a clone had made the context unknown.
WEB_HOSTS = tuple(
    ["docs.onyx.app", "onyx.app", "docs.pipeshub.com", "pipeshub.com", "github.com"]
    + [f"blog{n}.pxh-run01.dev" for n in range(17)]
)
SEARCH_DESTINATION = "SearXNG"
# A recorded run: the harness's hand-off steer, which the root model copied into the engine's task (its words before and
# after ADR-RUN-01 D5 dropped the "[PipesHub]" tag).
STEER_MARK = "Hand it to the coding engine now"
# What that run's engine did with a task that told it to call code_task: looked for the tool, found none, stopped.
REFUSED_HAND_OFF = "There is no code_task tool in this run, so I cannot hand this task to the coding engine."


@dataclass(frozen=True)
class Shape:
    """How big the run is. ``ONYX`` is the recorded one; a lane that cannot carry it says which field it scales and why."""

    files_per_repo: int = 10_000
    web_reads: int = 150
    report_bytes: int = 140 * 1024
    appends: int = 7
    # Virtual seconds a model step takes: gathering, each append, and the last step the limit interrupts.
    gather_step_s: float = 8.0
    append_step_s: float = 210.0
    last_step_s: float = 318.0
    working_s: int = 1800
    reads_per_step: int = 5


ONYX = Shape()


# --- the web ---------------------------------------------------------------------------------------------------------


def web_read(n: int) -> tuple[str, dict[str, Any]]:
    """The ``n``-th public read: a fetch of a page on one of the hosts, every fifth a search."""
    if n % 5 == 4:
        return WEB_SEARCH, {"query": f"onyx vs pipeshub finding {n}"}
    host = WEB_HOSTS[n % len(WEB_HOSTS)]
    return FETCH_URL, {"url": f"https://{host}/finding-{n}"}


def web_reads(shape: Shape) -> list[tuple[str, dict[str, Any]]]:
    return [web_read(n) for n in range(shape.web_reads)]


# WP-103: PipesHub's public personal-access-token page, whose paste block holds an example token of the
# ``pipeshub_pat`` shape; the engine printed it from its notes and the next model call was refused. Built at run time
# so this source holds no literal credential shape.
SECRET_SHAPED_URL = "https://docs.pipeshub.com/finding-2"
SECRET_SHAPED_KIND = "pipeshub_pat"


def secret_shaped_example() -> str:
    return "ph" + "pat_" + "Ab3dEf6hIj9lMn2pQr5t"


def page_text(url: str) -> str:
    """A public page: what the fake web answers ``fetch_url`` with (the URL, a title, a few paragraphs)."""
    body = " ".join(f"Paragraph {n} of {url} describes a capability in detail." for n in range(12))
    if url == SECRET_SHAPED_URL:
        body += f"\n```\nPIPESHUB_MCP_URL=https://your-pipeshub-instance.com/mcp\nPIPESHUB_MCP_TOKEN={secret_shaped_example()}...\n```"
    return f"url: {url}\ntitle: {url.rsplit('/', 1)[-1]}\ncontent: {body}"


def search_urls(query: str) -> list[str]:
    del query
    return [f"https://{WEB_HOSTS[n]}/result-{n}" for n in range(5)]


def search_text(query: str) -> str:
    hits = "\n".join(f"- {url}: {query} ({n})" for n, url in enumerate(search_urls(query)))
    return f"Results for {query}:\n{hits}"


def cited_web_urls(shape: Shape) -> list[str]:
    return [str(args["url"]) for tool, args in web_reads(shape) if tool == FETCH_URL]


# --- the repositories ------------------------------------------------------------------------------------------------


def repository_url(repository: str) -> str:
    return f"https://{GIT_HOST}/{repository}"


def repository_file(index: int) -> str:
    return f"src/pkg{index // 100:03d}/mod{index:05d}.py"


def repository_link(repository: str, commit: str, index: int) -> str:
    """A link into a fetched repository at the fetched commit (``url@commit:path``, WP-83's criterion-2 reading)."""
    return f"https://{GIT_HOST}/{repository}/blob/{commit}/{repository_file(index)}"


_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}
# One commit at a fixed time by a fixed author: the same files make the same commit on every host and in a sandbox.
_COMMIT_HEADER = "commit refs/heads/main\ncommitter t <t@pxh-run01.test> 1767225600 +0000\ndata 5\nseed\n"


def _module(repository: str, index: int) -> str:
    return f"# {repository} module {index}\nVALUE = {index}\n"


def fast_import_stream(repository: str, files: int) -> bytes:
    """The fixture repository as a ``git fast-import`` stream: ``files`` small Python files in one commit."""
    chunks = [_COMMIT_HEADER.encode()]
    for index in range(files):
        body = _module(repository, index).encode()
        chunks.append(f"M 100644 inline {repository_file(index)}\ndata {len(body)}\n".encode() + body + b"\n")
    return b"".join(chunks)


def fast_import_bash(repository: str, files: int, bare: str) -> str:
    """One bash line that builds the same repository at ``bare`` with the sandbox's own git (byte for byte the stream
    ``fast_import_stream`` makes, so the commit is the same)."""
    header = _COMMIT_HEADER.replace("\n", "\\n")
    return (
        f"git init -q --bare -b main {bare} && {{ printf '{header}'; "
        f"for i in $(seq 0 {files - 1}); do printf -v b '# {repository} module %d\\nVALUE = %d\\n' $i $i; "
        "printf -v p 'src/pkg%03d/mod%05d.py' $((i / 100)) $i; "
        "printf 'M 100644 inline %s\\ndata %d\\n%s\\n' \"$p\" ${#b} \"$b\"; done; } "
        f"| git -C {bare} fast-import --quiet"
    )


def fixture_repository(root: Path, repository: str, files: int) -> tuple[Path, str]:
    """``root/<repository>.git`` built from ``fast_import_stream`` (a second for 10,000 files), and its commit."""
    bare = root / f"{repository}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    env = {"PATH": os.environ["PATH"], "HOME": str(root), **_GIT_ENV}
    _ = subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, env=env, timeout=60)
    _ = subprocess.run(
        ["git", "fast-import", "--quiet"], input=fast_import_stream(repository, files), cwd=bare, check=True,
        env=env, timeout=120,
    )
    head = subprocess.run(
        ["git", "rev-parse", "refs/heads/main"], cwd=bare, check=True, env=env, capture_output=True, text=True, timeout=60
    ).stdout.strip()
    return bare, head


# --- the git host both lanes' worker fetches from ----------------------------------------------------------------


@dataclass
class GitFixture:
    host: GitHttpHost
    ca_file: str
    port: int
    commits: dict[str, str]
    root: Path


async def git_fixture(root: Path, files: int) -> GitFixture:
    """The two fixture repositories behind a local https ``git http-backend`` for ``GIT_HOST``, with its authority."""
    commits = {repo: fixture_repository(root / "srv", repo, files)[1] for repo in REPOSITORIES}
    ca, cert, key = https_pki([GIT_HOST])
    for name, pem in (("ca.pem", ca), ("cert.pem", cert), ("key.pem", key)):
        (root / name).write_text(pem)
    tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    tls.load_cert_chain(str(root / "cert.pem"), str(root / "key.pem"))
    host = GitHttpHost(root / "srv")
    _ = await host.start("127.0.0.1", tls=tls)
    return GitFixture(host, str(root / "ca.pem"), host.port, commits, root)


def git_hosts() -> list[dict[str, Any]]:
    """The allowed git hosts as an admin configured them: the stand-in for github.com's public repositories, marked
    readable by anyone (WP83-FU-3). Without the mark a clone is not public, and every later web read asks."""
    return [{"host": GIT_HOST, "publicReadable": True}]


class LoopbackFetcher(RepositoryFetcher):
    """Production's fetch, dialling the local host's loopback address, which stands for the public one it is."""

    @override
    async def checked_address(self, host: str) -> str:
        assert host == GIT_HOST, host
        return "127.0.0.1"

    @override
    def address_public(self, address: str) -> bool:
        return address == "127.0.0.1"


class NoResolver:
    async def resolve(self, host: str) -> tuple[str, ...]:
        raise AssertionError(f"the loopback fetcher resolves nothing ({host})")


# --- the report ------------------------------------------------------------------------------------------------------


def report_sections(shape: Shape, commits: Mapping[str, str]) -> list[str]:
    """The report in ``shape.appends`` parts of about equal size, each citing pages it read and files of the fetched
    repositories at the fetched commits. The first part opens with the title and the executive summary."""
    urls = cited_web_urls(shape)
    per = shape.report_bytes // shape.appends
    parts: list[str] = []
    for part in range(shape.appends):
        lines = ["# Onyx vs PipesHub\n", "## Executive summary\n", f"Onyx documents its connectors ([docs]({urls[0]})).\n"] if part == 0 else []
        lines.append(f"## Part {part + 1}\n")
        n = 0
        while sum(len(line) for line in lines) < per:
            url = urls[(part * 7 + n) % len(urls)]
            repository = REPOSITORIES[n % len(REPOSITORIES)]
            link = repository_link(repository, commits.get(repository, "0" * 40), (part * 31 + n * 17) % 1000)
            lines.append(f"- Finding {part}.{n}: shown in [the page]({url}) and [the code]({link}).\n")
            n += 1
        parts.append("".join(lines))
    return parts


def append_command(path: str, text: str) -> str:
    """One shell line that appends ``text`` to ``path`` (real bash and the in-process shell read it the same way)."""
    escaped = text.replace("\\", "\\\\").replace("\n", "\\n")
    assert "'" not in escaped, "the report's text is single-quoted for printf"
    folder = path.rsplit("/", 1)[0] if "/" in path else "."
    # A real sandbox starts without the folder (the Docker lane's first run: "out/report.md: No such file").
    return f"mkdir -p {folder} && printf '%b' '{escaped}' >> {path}"


# --- the scripts -----------------------------------------------------------------------------------------------------


def _batches(reads: Sequence[tuple[str, dict[str, Any]]], size: int) -> list[str]:
    """Reads as one model step per ``size`` calls of one tool, as an engine batches parallel calls."""
    lines: list[str] = []
    for tool in (FETCH_URL, WEB_SEARCH):
        mine = [args for name, args in reads if name == tool]
        lines.extend(f"tools {tool} {json.dumps(mine[at : at + size])}" for at in range(0, len(mine), size))
    return lines


def research_script(
    shape: Shape, commits: Mapping[str, str], clone: Callable[[str], Sequence[str]], *, closing: str = "say Done."
) -> str:
    """The engine's whole task as scripted-model directives: half the reads, the clones (``clone(url)``: the lane's
    lines for one), the other half, then each append in a step of its own (``hold``: the lane's clock moves
    ``append_step_s`` while the step runs), and a last step that the limit interrupts."""
    reads = web_reads(shape)
    half = len(reads) // 2
    lines = [REQUEST, *_batches(reads[:half], shape.reads_per_step)]
    for repository in REPOSITORIES:
        lines.extend(clone(repository_url(repository)))
    lines.extend(_batches(reads[half:], shape.reads_per_step))
    for section in report_sections(shape, commits):
        lines.extend(["hold", f"shell {append_command(REPORT, section)}"])
    lines.extend(["hold", f"shell {append_command('notes/polish.md', 'Polishing the report.')}", closing])
    return "\n".join(lines)


def nothing_script() -> str:
    """The child of the hand-off steer's run: it ends its turn at once, having read nothing and written nothing."""
    return "\n".join([REQUEST, "say I could not start this task."])


def leaked_steer(instruction: str) -> str:
    """The parent's brief as that run's root model wrote it: the person's request with the harness's own steer copied in
    (``instruction``: the harness's up-front hand-off instruction, ``handoff_instructions.upfront``)."""
    return f"{REQUEST}\n\nNotes from the conversation: {instruction}"


# --- the web tools (both lanes) ----------------------------------------------------------------------------------


def web_tool(text: Callable[[dict[str, Any]], str], urls: Callable[[dict[str, Any]], list[str]]) -> Any:  # noqa: ANN401
    """A public web tool as PipesHub's journals a result: its text, and the pages it read as the call's citation delta
    (what ``call_provenance`` turns into the result's ``WebPage`` sources)."""
    from app.harness.citations import EVENT as CITATIONS_EVENT
    from harness_core.effects.types import ToolExecution
    from harness_core.journal.types import NewEvent

    async def execute(call: Any, ctx: Any) -> ToolExecution:  # noqa: ANN401
        args = dict(call.arguments)
        body = text(args)
        records = [{"url": url, "title": url.rsplit("/", 1)[-1], "content": body[:400]} for url in urls(args)]
        delta = NewEvent(CITATIONS_EVENT, {"callId": ctx.invocation_id, "webRecords": records}, critical=False)
        return ToolExecution(status="ok", content=body, events=(delta,))

    return execute


def web_handlers() -> dict[str, Any]:
    """``fetch_url`` and ``web_search`` over the fake web, by tool name."""
    return {
        FETCH_URL: web_tool(lambda args: page_text(str(args.get("url"))), lambda args: [str(args.get("url"))]),
        WEB_SEARCH: web_tool(lambda args: search_text(str(args.get("query"))), lambda args: search_urls(str(args.get("query")))),
    }


def web_rules() -> dict[str, Any]:
    """The production provenance rule of each web tool."""
    from harness_core.provenance.rules import WEB_RULE

    return {FETCH_URL: WEB_RULE, WEB_SEARCH: WEB_RULE}


def research_catalog() -> tuple[Any, dict[str, bool]]:
    """``fetch_url`` and ``web_search`` over the fake web, as the Docker lane's worker grants them, and each one's
    idempotency (``durable_stack``'s ``catalog="research"``)."""
    from harness_core.contracts.generated.sink import SinkDescriptor
    from harness_core.effects.types import ToolEffect
    from harness_core.invoker import CatalogTool, InMemoryToolCatalog
    from harness_core.kernel.core.tool_schema import ToolSchema

    handlers = web_handlers()

    def tool(name: str, tool_id: str, field_name: str) -> Any:  # noqa: ANN401
        schema = ToolSchema(
            name=name, description=f"Public web: {field_name}.",
            input_schema={"type": "object", "properties": {field_name: {"type": "string"}}, "required": [field_name]},
        )
        effect = ToolEffect(tool_id, "read", True, SinkDescriptor(egress="external", args_leave_boundary=True))
        return CatalogTool(schema, effect, handlers[name])

    tools = [tool(FETCH_URL, FETCH_URL_ID, "url"), tool(WEB_SEARCH, WEB_SEARCH_ID, "query")]
    return InMemoryToolCatalog(tools, [FETCH_URL_ID, WEB_SEARCH_ID]), {FETCH_URL_ID: True, WEB_SEARCH_ID: True}


# --- reading what the run did (public seams only) --------------------------------------------------------------------


@dataclass
class Observed:
    """What a person and an operator can see of one research run, read from its journal and stores."""

    status: str
    # ``run.completed`` of the run the person talks to (the root), as the timeline reads it.
    completed: Mapping[str, Any]
    answer: str
    # The deliverable as the person can open it: its content and the artifact version.
    report: bytes | None
    report_version: str | None
    report_lifecycle: str | None
    # The conversation's saved files (the workspace head) and how many commits the store refused.
    saved_files: frozenset[str]
    refused_saves: int
    # Every approval card the person was shown: (tool id, rule, destination).
    cards: list[tuple[str, str, str]]
    # Sources the person's answer cites, as the chat lists them.
    citations: list[str]
    # Each run's claims (an attempt is a claim): a from-scratch redrive shows as a second claim of the engine run.
    claims: Mapping[str, int]
    # The engine child's (or engine run's) events, and the model calls its engine made.
    engine_events: list[Any] = field(default_factory=list[Any])
    engine_requests: list[str] = field(default_factory=list[str])
    # The engine's instructions on its first model call (its allowance and time plan).
    engine_system: str = ""
    # The root run's deadline, epoch seconds (the child's slice is taken from it).
    parent_deadline_at_s: float | None = None
    # The engine's tool calls (name, arguments) and the results it was handed, in order.
    engine_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list[tuple[str, dict[str, Any]]])
    engine_results: list[str] = field(default_factory=list[str])
    events: list[Any] = field(default_factory=list[Any])
    # Tokens the provider billed and the tokens the run's journal charged.
    billed_tokens: int = 0
    journaled_tokens: int = 0
    # The virtual clock: when the writing window's steer reached the engine, when each model step began, and when
    # the run's work stopped (the engine's turn ended or was cut).
    steered_at_s: float | None = None
    step_starts_s: list[float] = field(default_factory=list[float])
    ended_at_s: float = 0.0
    # D2: the saved head's store-only references record (address and version per checkout), if it has one.
    references_record: bytes | None = None
    # D6: each fault the store raised on a commit, in order.
    injected_faults: list[str] = field(default_factory=list[str])


def outcome_facts(events: Iterable[Any]) -> list[Mapping[str, Any]]:
    """ADR-RUN-01 D1 (WP-95's schema): the ledger's facts (``x.pipeshub.outcome.recorded``) and the settled outcome
    (``x.pipeshub.outcome.settled``), each as ``{"fact": <the ADR's name>, ...its wire}``: ``ArtifactWritten``,
    ``LimitReached``, ``SaveRefused``, …, and ``Completed``, ``Partial`` or ``Failed``."""
    found: list[Mapping[str, Any]] = []
    for event in events:
        if event.type == "x.pipeshub.outcome.recorded":
            found.extend(
                {"fact": _adr_name(str(f.get("kind"))), **f} for f in event.data.get("facts") or [] if isinstance(f, dict)
            )
        elif event.type == "x.pipeshub.outcome.settled" and isinstance(event.data.get("outcome"), dict):
            found.append({"fact": _adr_name(str(event.data["outcome"].get("kind"))), **event.data["outcome"]})
    return found


def _adr_name(kind: str) -> str:
    return "".join(part.capitalize() for part in kind.split("_"))


def flow_cards(cards: Iterable[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """The cards the flow rule raised for public reads (``fetch_url``, ``web_search``)."""
    return [c for c in cards if c[0] in (FETCH_URL_ID, WEB_SEARCH_ID)]


def clone_cards(cards: Iterable[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    return [c for c in cards if c[0].endswith("/repository_fetch")]


# --- research depth: the research criteria a harness can be held to without a model (R39-RESEARCH) --------------------

_LINK = re.compile(r"\]\((https://[^)\s]+)\)")
_HEADING = re.compile(r"^## ", re.MULTILINE)


@dataclass(frozen=True)
class ResearchDepth:
    """How deep one scripted research run went, end to end, as seven research criteria read a live one. The
    scripted model does what its script says, so any shortfall here
    is the harness's: a read refused or never reached, an ask that stopped the work, a report or citation lost on the
    way to the person. The model-judged half (coverage, accuracy, plan quality) is not measured here."""

    reads_issued: int
    reads_ok: int
    clones_used: int
    report_bytes: int
    sections: int
    cited_web: int
    cited_web_read: int
    cited_code: int
    engine_turns: int
    clone_cards: int
    flow_cards: int

    def shortfalls(self, shape: Shape) -> list[str]:
        """What falls short of the shape the script played; empty when nothing was cut."""
        floor = {
            "reads_issued": shape.web_reads,
            "reads_ok": shape.web_reads,
            "clones_used": len(REPOSITORIES) if shape.files_per_repo else 0,
            "report_bytes": shape.report_bytes,
            # The executive summary and one heading per append.
            "sections": shape.appends + 1,
        }
        found = [f"{name} {getattr(self, name)} < {least}" for name, least in floor.items() if getattr(self, name) < least]
        if self.cited_web_read < self.cited_web or self.cited_web == 0:
            found.append(f"cited web pages read {self.cited_web_read} of {self.cited_web}")
        if shape.files_per_repo and self.cited_code == 0:
            found.append("no link into a fetched repository")
        if self.engine_turns != 1:
            found.append(f"engine turns {self.engine_turns} != 1")
        if self.flow_cards or self.clone_cards > len(REPOSITORIES):
            found.append(f"cards: {self.clone_cards} clone, {self.flow_cards} flow")
        return found


def research_depth(observed: Observed) -> ResearchDepth:
    """``observed``'s depth, from what the engine called and was handed, the delivered report and the child's journal."""
    reads = [args for name, args in observed.engine_calls if name in (FETCH_URL, WEB_SEARCH)]
    web = {
        str(e.data.get("invocationId")) for e in observed.engine_events
        if e.type == "tool.intent" and str(e.data.get("toolId")) in (FETCH_URL_ID, WEB_SEARCH_ID)
    }
    ok = [
        e for e in observed.engine_events
        if e.type == "tool.outcome" and e.data.get("status") == "ok" and str(e.data.get("invocationId")) in web
    ]
    report = observed.report.decode() if observed.report is not None else ""
    links = set(_LINK.findall(report))
    code = {u for u in links if u.startswith(f"https://{GIT_HOST}/")}
    pages = links - code
    read = {str(args.get("url")) for name, args in observed.engine_calls if name == FETCH_URL}
    return ResearchDepth(
        reads_issued=len(reads),
        reads_ok=len(ok),
        clones_used=sum(r.startswith("Cloned ") for r in observed.engine_results),
        report_bytes=len(report.encode()),
        sections=len(_HEADING.findall(report)),
        cited_web=len(pages),
        cited_web_read=len(pages & read),
        cited_code=len(code),
        engine_turns=sum(1 for e in observed.engine_events if e.type == "turn.started"),
        clone_cards=len(clone_cards(observed.cards)),
        flow_cards=len(flow_cards(observed.cards)),
    )


__all__ = [
    "FETCH_URL",
    "FETCH_URL_ID",
    "GIT_HOST",
    "ONYX",
    "REFUSED_HAND_OFF",
    "REPORT",
    "REPORT_NAME",
    "REPOSITORIES",
    "REQUEST",
    "SECRET_SHAPED_KIND",
    "SECRET_SHAPED_URL",
    "STEER_MARK",
    "WEB_SEARCH",
    "WEB_SEARCH_ID",
    "Observed",
    "ResearchDepth",
    "Shape",
    "append_command",
    "cited_web_urls",
    "clone_cards",
    "fast_import_bash",
    "fast_import_stream",
    "fixture_repository",
    "flow_cards",
    "leaked_steer",
    "nothing_script",
    "outcome_facts",
    "page_text",
    "report_sections",
    "repository_file",
    "repository_link",
    "repository_url",
    "research_catalog",
    "research_depth",
    "research_script",
    "search_text",
    "search_urls",
    "secret_shaped_example",
    "web_read",
    "web_reads",
]
