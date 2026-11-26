"""R3 end to end on a real engine: ``ph`` in the engine's own shell reaches the run's
tool through the real egress proxy and the real tool gateway, as the engine's MCP client would.

Each engine's controller lane (opencode ``test_real_controller``, Codex ``test_real_codex_controller``, Claude Code
``test_real_claude_code_controller``) runs one turn whose scripted model has the engine run ``ph`` from its shell;
the session folder is the production one (``pipeshub_tool_folder``). Codex strips ``*TOKEN*`` variables from its
shell, so its ``ph`` reads the placeholder from ``/pipeshub/session/gateway.json``.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_core.testing.engines.gateway import GrantedTool, args_digest
from harness_engines import pipeshub
from harness_engines.pipeshub import SESSION_PH_PATH, VERSION, ph, pipeshub_tool_folder

if TYPE_CHECKING:
    from harness_core.engines.controller import EngineController

ARGUMENTS = {"q": "ph-e2e"}
PROMPT = (
    "PH-E2E\n"
    "shell mkdir -p out && ph --version > out/ph-version.txt 2> out/ph-err.txt"
    " && cat /pipeshub/tools.txt > out/ph-index.txt 2>> out/ph-err.txt"
    " && cat /pipeshub/tools/INDEX.md > out/ph-tools.md 2>> out/ph-err.txt"
    f" && ph tools call search --json '{json.dumps(ARGUMENTS)}' > out/ph-call.txt 2>> out/ph-err.txt\n"
    "say done"
)


SDK_ARGUMENTS = {"q": "sdk-e2e"}
# WS-14 (C3.15, C3.16): the same tool called from the engine's shell three ways, with the session folder's stubs
# found as the base prompt and tools/INDEX.md say: ph, the Python stub on the image's CPython and the TypeScript stub
# on the image's Node.
SDK_PROMPT = (
    "SDK-E2E\n"
    f"shell mkdir -p out && ph tools call search --json '{json.dumps(SDK_ARGUMENTS)}' > out/sdk-ph.txt 2> out/sdk-err.txt"
    f" && PYTHONPATH={pipeshub.SDK_PATH}/python python3 -c"
    " 'from pipeshub_tools import pipeshub; print(pipeshub.search(q=\"sdk-e2e\").text)' > out/sdk-py.txt 2>> out/sdk-err.txt"
    " && node --input-type=module -e"
    f" 'const {{ pipeshub }} = await import(\"{pipeshub.SDK_PATH}/ts/index.mjs\");"
    " console.log((await pipeshub.search({ q: \"sdk-e2e\" })).text)' > out/sdk-ts.txt 2>> out/sdk-err.txt"
    " && grep -c PYTHONPATH /pipeshub/tools/INDEX.md > out/sdk-index.txt 2>> out/sdk-err.txt\n"
    "say done"
)


def assert_stubs_called_the_tool(outcome: Any, head: dict[str, bytes], gateway: Any) -> None:  # noqa: ANN401
    """The Python and TypeScript stubs answered what ``ph tools call`` answered, through the same gateway."""
    assert (outcome.reason, outcome.final_text.strip()) == ("completed", "done"), outcome
    shown = {path: content.decode(errors="replace") for path, content in head.items() if path.startswith("out/sdk-")}
    assert head.get("out/sdk-err.txt", b"") == b"", shown
    expected = f"RESULT for search {json.dumps(SDK_ARGUMENTS, sort_keys=True)}"
    assert [head[f"out/sdk-{way}.txt"].decode().strip() for way in ("ph", "py", "ts")] == [expected] * 3, shown
    assert gateway.executions[("search", args_digest(SDK_ARGUMENTS))] >= 1, (dict(gateway.executions), shown)
    assert head["out/sdk-index.txt"].decode().strip() == "1", shown


# WP70-SESSIONPH: on the CURRENT images (their /usr/local/bin/ph predates WP-70), the engine's shell and a login shell
# find the session folder's ph first, and it is WP-70's: it finds the listed repository tool, names ``ph git clone``
# beside it, and resolves the engine's server-prefixed name.
REPOSITORY = GrantedTool(
    "repository_fetch",
    "Clone a public git repository into this sandbox, read-only. Run `ph git clone <https url>` from your shell.",
)
SESSION_PH_PROMPT = (
    "SESSION-PH\n"
    "shell mkdir -p out && command -v ph > out/sph-path.txt 2> out/sph-err.txt"
    " && bash -lc 'command -v ph' >> out/sph-path.txt 2>> out/sph-err.txt"
    " && ph tools search repository > out/sph-search.txt 2>> out/sph-err.txt"
    " && bash -lc 'ph tools describe pipeshub__repository_fetch' > out/sph-describe.txt 2>> out/sph-err.txt\n"
    "say done"
)


def assert_session_ph_found_the_repository_tool(outcome: Any, head: dict[str, bytes]) -> None:  # noqa: ANN401
    assert (outcome.reason, outcome.final_text.strip()) == ("completed", "done"), outcome
    shown = {path: content.decode(errors="replace") for path, content in head.items() if path.startswith("out/sph-")}
    assert head.get("out/sph-err.txt", b"") == b"", shown
    assert head["out/sph-path.txt"].decode().split() == [SESSION_PH_PATH, SESSION_PH_PATH], shown
    search = head["out/sph-search.txt"].decode()
    assert "repository_fetch" in search and "(run: ph git clone <https url>)" in search, shown
    assert "Clone a public git repository" in head["out/sph-describe.txt"].decode(), shown


def with_tool_folder(controller: EngineController) -> None:
    """The lane's controller as the worker builds it (``controller_config``): with the ``/pipeshub`` session folder."""
    config = controller._config  # pyright: ignore[reportPrivateUsage]
    controller._config = dataclasses.replace(config, tool_folder=pipeshub_tool_folder())  # pyright: ignore[reportPrivateUsage]


def assert_ph_called_the_tool(outcome: Any, head: dict[str, bytes], gateway: Any) -> None:  # noqa: ANN401
    assert (outcome.reason, outcome.final_text.strip()) == ("completed", "done"), outcome
    shown = {path: content.decode(errors="replace") for path, content in head.items() if path.startswith("out/ph-")}
    assert head.get("out/ph-err.txt", b"") == b"", shown
    # The tool ran once, through the gateway, with ph's arguments: what an MCP call of the engine would have done.
    assert gateway.executions[("search", args_digest(ARGUMENTS))] == 1, (dict(gateway.executions), shown)
    assert head["out/ph-call.txt"].decode().strip() == f"RESULT for search {json.dumps(ARGUMENTS, sort_keys=True)}"
    assert head["out/ph-index.txt"].decode().splitlines() == ["search: Search the knowledge base"]
    # Contract v1 (WS-01): the image's links reach this turn's tools folder.
    assert "- pipeshub (1): search" in head["out/ph-tools.md"].decode()
    assert head["out/ph-version.txt"].decode().strip() == f"ph {ph.PH_VERSION} (/pipeshub contract {VERSION})"


# C3.12 (WS-15): ``ph skill``, ``ph publish`` and ``ph ask`` in the engine's own shell, through the egress proxy and
# the real tool gateway. They run the worker's ``ph`` (the session folder's ``ph.py``, seeded at each turn): the
# image's ``/usr/local/bin/ph`` is the same file and gains these commands with the next engine image.
SESSION_PH = f"python3 {pipeshub.SESSION_DIR}/{pipeshub.HOOK_PROGRAM_FILE}"
C312_PROMPT = (
    "PH-C312\n"
    "shell printf '# Q3' > notes.md"
    f" && {SESSION_PH} skill list > ph-skill-list.txt 2> ph-c312-err.txt"
    f" && {SESSION_PH} skill show report > ph-skill-show.txt 2>> ph-c312-err.txt"
    f" && {SESSION_PH} publish notes.md --as ph-published.md > ph-publish.txt 2>> ph-c312-err.txt"
    f" && {SESSION_PH} --json ask 'Which region?' --option EU --option US > ph-ask.json 2>> ph-c312-err.txt\n"
    "say done"
)


# C5.01 (J-26): a large input waits for its first read; the engine places it with the session's ph from its shell.
BIG_INPUT = bytes(range(256)) * 64
SMALL_INPUT = b"region,amount\nnorth,1\n"
INPUTS_SEED = {"in/big.bin": BIG_INPUT, "in/small.csv": SMALL_INPUT}
INPUTS_PROMPT = (
    "LAZY-INPUTS\n"
    "shell mkdir -p out && ls in > out/li-before.txt 2> out/li-err.txt"
    " && python3 /pipeshub/session/ph.py inputs get big.bin > out/li-get.txt 2>> out/li-err.txt"
    " && sha256sum in/big.bin | cut -c1-64 > out/li-sum.txt 2>> out/li-err.txt"
    " && ls in > out/li-after.txt 2>> out/li-err.txt\n"
    "say done"
)


async def run_c312_turn(lane: Any, tenant: str, skill_md: bytes, task: Any) -> tuple[Any, Any, dict[str, bytes]]:  # noqa: ANN401
    """One turn of ``C312_PROMPT`` on ``lane``, its run pinned to the ``report`` skill, with a person to ask."""
    from harness_core.skills.types import SkillPin, SkillRef

    with_tool_folder(lane.controller)
    lane.skills.publish(tenant, "report", {"SKILL.md": skill_md})
    bundle = await lane.builder.build(lane.scope, SkillRef("report"))
    lane.gateway(1).asking()
    run = lane.run(1, skills=(SkillPin("report", bundle.digest),))
    outcome = await lane.controller.run_turn(run, task(0, C312_PROMPT))
    return run, outcome, await lane.head()


async def assert_c312_commands_worked(lane: Any, run: Any, outcome: Any, head: dict[str, bytes], skill_md: bytes) -> None:  # noqa: ANN401
    assert (outcome.reason, outcome.final_text.strip()) == ("completed", "done"), outcome
    shown = {path: content.decode(errors="replace") for path, content in head.items() if path.startswith("ph-")}
    assert head.get("ph-c312-err.txt", b"") == b"", shown
    # ph skill: the run's pinned skill, read from its read-only skills folder (SK-05).
    assert head["ph-skill-list.txt"].decode().strip() == "report: Use when asked for a report.", shown
    assert head["ph-skill-show.txt"].decode().startswith(skill_md.decode().rstrip("\n")), shown
    # ph publish: the file is in out/, and the gateway's answer names the draft the save before the call published.
    assert head["out/ph-published.md"] == b"# Q3"
    [draft] = [p for p in outcome.published if p.path == "ph-published.md"]
    assert head["ph-publish.txt"].decode().strip() == (
        f"Published ph-published.md (version {draft.version}, a draft until this task's turn ends). It is in the chat now."
    ), shown
    # The same artifact a file written to out/ makes: final once the turn ended.
    cards = [(c.name, c.lifecycle) for c in (await lane.publications.history(run.binding)).cards]
    assert ("ph-published.md", "draft") in cards and ("ph-published.md", "final") in cards, cards
    # ph ask: the run's ask_user, answered as the engine's own MCP call is (the question card is shown).
    asked = json.loads(head["ph-ask.json"])
    assert asked["ok"] is True and asked["command"] == "ask", shown
    assert asked["data"]["tool"] == "ask_user" and asked["data"]["question"] == "Which region?", shown
    assert asked["data"]["text"].startswith("Your question is shown to the person."), shown


def with_input_staging(controller: EngineController, max_bytes: int = 1024) -> None:
    """The lane's controller with inputs over ``max_bytes`` waiting for their first read."""
    config = controller._config  # pyright: ignore[reportPrivateUsage]
    controller._config = dataclasses.replace(config, input_stage_max_bytes=max_bytes)  # pyright: ignore[reportPrivateUsage]


def assert_input_staged_on_first_read(outcome: Any, head: dict[str, bytes]) -> None:  # noqa: ANN401
    """The engine started without the large input, placed it with ``ph inputs get``, and read the same bytes; the
    workspace holds it unchanged."""
    import hashlib

    assert (outcome.reason, outcome.final_text.strip()) == ("completed", "done"), outcome
    shown = {path: content.decode(errors="replace") for path, content in head.items() if path.startswith("out/li-")}
    assert head.get("out/li-err.txt", b"") == b"", shown
    assert head["out/li-before.txt"].decode().split() == ["small.csv"], shown
    assert head["out/li-get.txt"].decode().strip() == f"Placed in/big.bin ({len(BIG_INPUT)} bytes).", shown
    assert head["out/li-sum.txt"].decode().strip() == hashlib.sha256(BIG_INPUT).hexdigest(), shown
    assert head["out/li-after.txt"].decode().split() == ["big.bin", "small.csv"], shown
    assert head["in/big.bin"] == BIG_INPUT


# SEC-FS-02: a hostile engine points ``in/`` at a root-owned folder before asking for a large input, asks for an input
# by a traversing name, and writes outputs and links whose names escape on some system.
HOSTILE_PROMPT = (
    "HOSTILE-NAMES\n"
    "shell mkdir -p out && rm -rf in && ln -s /etc in"
    " && (python3 /pipeshub/session/ph.py inputs get big.bin > out/h-get.txt 2>&1; true)"
    " && (python3 /pipeshub/session/ph.py inputs get ../../etc/passwd > out/h-trav.txt 2>&1; true)"
    " && (test -e /etc/big.bin && echo PLACED || echo ABSENT) > out/h-etc.txt"
    " && printf x > out/CON.txt && printf y > 'out/a%2fb.txt' && printf z > 'out/report.'"
    " && ln -s '..\\..\\x' out/back && mkdir -p 'C:' && printf w > 'C:/evil.txt'"
    " && printf ok > out/fine.txt\n"
    "say done"
)


# J-26 (WP-69): a 50 MB PDF a person gave the run, read with the pdf skill's library once the engine placed it.
J26_PAGES = 3
J26_PAD_BYTES = 50 * 1000 * 1000
J26_SKILL = (
    Path(__file__).resolve().parents[4] / "app/agents/agent_loop/skills/builtin_packs/pdf/SKILL.md"
).read_bytes()
J26_PROMPT = (
    "J26-PDF\n"
    "shell mkdir -p out && (test -e in/report.pdf && echo present || echo absent) > out/j26-before.txt 2> out/j26-err.txt"
    " && grep -c pypdf /skills/pdf/SKILL.md > out/j26-skill.txt 2>> out/j26-err.txt"
    " && python3 /pipeshub/session/ph.py inputs get report.pdf > out/j26-get.txt 2>> out/j26-err.txt"
    " && python3 -c \"import pypdf; r = pypdf.PdfReader('in/report.pdf'); print(len(r.pages));"
    " print(r.pages[-1].extract_text())\" > out/j26-text.txt 2>> out/j26-err.txt"
    " && ls in > out/j26-after.txt 2>> out/j26-err.txt\n"
    "say done"
)


def assert_hostile_names_refused(outcome: Any, head: dict[str, bytes]) -> None:  # noqa: ANN401
    """Nothing landed in the root-owned folder; the traversing name named no input; outputs no system holds as one
    file were refused, not published; names with a drive or an encoded separator and the backslash link stayed out
    of the workspace."""
    from harness_core.engines.controller import OUTPUT_UNSAFE_NAME

    assert (outcome.reason, outcome.final_text.strip()) == ("completed", "done"), outcome
    shown = {path: content.decode(errors="replace") for path, content in head.items() if path.startswith("out/h-")}
    assert head["out/h-etc.txt"] == b"ABSENT\n", shown
    assert "was not placed" in shown["out/h-get.txt"], shown
    assert "is no input waiting" in shown["out/h-trav.txt"], shown
    refused = {(r.path, r.reason_code) for r in outcome.refused_outputs}
    assert {("CON.txt", OUTPUT_UNSAFE_NAME), ("report.", OUTPUT_UNSAFE_NAME)} <= refused, refused
    published = {p.path for p in outcome.published}
    assert "fine.txt" in published and not published & {"CON.txt", "report."}, published
    assert not {"out/a%2fb.txt", "C:/evil.txt", "out/back"} & set(head), sorted(head)


def big_pdf(pages: int = J26_PAGES, pad_bytes: int = J26_PAD_BYTES) -> bytes:
    """A valid PDF of ``pages`` text pages ("J26 page n") whose size is mostly one unreferenced binary stream, as a
    scanned report's images make a real one large."""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + b" ".join(f"{4 + 2 * i} 0 R".encode() for i in range(pages)) + b"] /Count "
        + str(pages).encode() + b" >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i in range(pages):
        text = f"BT /F1 18 Tf 72 720 Td (J26 page {i + 1}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {5 + 2 * i} 0 R >>".encode()
        )
        objects.append(b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text + b"\nendstream")
    pad = (bytes(range(256)) * (pad_bytes // 256 + 1))[:pad_bytes]
    objects.append(b"<< /Length " + str(len(pad)).encode() + b" >>\nstream\n" + pad + b"\nendstream")
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def assert_pdf_read_on_first_read(outcome: Any, head: dict[str, bytes], pdf: bytes) -> None:  # noqa: ANN401
    """The turn started without the PDF, placed it with ``ph inputs get``, read every page with pypdf (the pdf
    skill's extraction library) and the workspace still holds the bytes it was given."""
    assert (outcome.reason, outcome.final_text.strip()) == ("completed", "done"), outcome
    shown = {path: content.decode(errors="replace") for path, content in head.items() if path.startswith("out/j26-")}
    assert head.get("out/j26-err.txt", b"") == b"", shown
    assert head["out/j26-before.txt"].decode().strip() == "absent", shown
    assert int(head["out/j26-skill.txt"].decode().strip()) > 0, shown
    assert head["out/j26-get.txt"].decode().strip() == f"Placed in/report.pdf ({len(pdf)} bytes).", shown
    assert head["out/j26-text.txt"].decode().split("\n", 1) == [str(J26_PAGES), f"J26 page {J26_PAGES}\n"], shown
    assert head["out/j26-after.txt"].decode().split() == ["report.pdf"], shown
    assert head["in/report.pdf"] == pdf
