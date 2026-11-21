"""PL, DS, UV (SK1): Anthropic's pptx and pdf skills, installed with the skills manager, on opencode and Codex.

E0's and E8's real lane: the release registry's adapter on its pinned binary (in the SK1 skill-runtime test
image), the Docker broker, the egress proxy, E2's tool gateway and E3's model gateway as the deployment's model
route, with the scripted model as E3's provider. ``PlacingBroker`` puts the user's installed skills tree,
unchanged, where the engine reads it (opencode: its ``skills.paths`` folder ``/skills``; Codex:
``$CODEX_HOME/skills``) and inspects it from inside the sandbox. The scripted model follows each skill's
instructions through the engine's own tools; everything is checked in this process on what the controller
published.

Needs ``SK1_SKILLS_DIR`` (the skills are not in this repository) and the images
``deployment/sandbox-images/skill-runtime/build.sh opencode|codex`` builds.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import io
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.harness.gateway.host import HttpToolGatewayHost
from harness_core.access.types import AccessRouteRef, PinnedRoute, route_digest
from harness_core.artifacts.memory import InMemoryArtifactStore, InMemoryPublicationLog
from harness_core.artifacts.publisher import WorkspaceArtifactPublisher
from harness_core.engines.checkpoints import ObjectStoreEngineCheckpoints
from harness_core.engines.controller import (
    EngineController,
    EngineControllerConfig,
    EngineRunContext,
)
from harness_core.engines.events import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    ToolCallSeen,
    turn_id_for,
)
from harness_core.engines.memory import InMemoryEngineEventSink
from harness_core.engines.types import EngineTask, FollowUp
from harness_core.interactions.ports import RunBinding
from harness_core.sandbox.types import Resources
from harness_core.skills.builder import StoredSkillBundleBuilder
from harness_core.skills.materializer import TarSkillMaterializer
from harness_core.skills.memory import InMemorySkillContentSource
from harness_core.testing.clock import ManualClock
from harness_core.testing.engines.endpoint_gateway import EndpointConformanceGateway
from harness_core.testing.engines.gateway import GrantedTool
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryKeyProvider,
    InMemoryWorkspaceStore,
    InMemoryWorkspaceWriters,
)
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.sync.strategies import SyncStrategies
from harness_core.workspace.sync.tar_sync import TarWorkspaceSync
from harness_core.workspace.types import ObjectScope, WorkspaceFile
from harness_engines.release import (
    release_allowlist,
    release_default_profile,
    release_registry,
)
from tests.unit.harness.engines.real_stack import (
    OWNER,
    ModelGatewayLane,
    _free_port,  # pyright: ignore[reportPrivateUsage]
    engine_broker,
    lane_available,
    start_stack,
    stop_stack,
)
from tests.unit.harness.engines.skill_packs import (
    PLACEMENTS,
    PlacingBroker,
    installed_dir,
    manifest,
    pdf_form_values,
    pdf_pages,
    pptx_parts,
    read_tree,
    slide_texts,
    tree_mismatch,
    words,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator, Mapping

    from harness_core.engines.controller import EngineTurnOutcome
    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import EngineVersion, JsonValue
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(1800, method="thread")]

ENGINES = ("opencode", "codex")
IMAGES = {
    "opencode": os.environ.get("SK1_OPENCODE_IMAGE", "pxh-sk1-opencode:test"),
    "codex": os.environ.get("SK1_CODEX_IMAGE", "pxh-sk1-codex:test"),
}
TENANT, SESSION = "tenant-sk1", "session-sk1"
GRANT = (GrantedTool("search", "Search the knowledge base."),)
MODEL_KEY = "sk1-run"
_E3_PORT: dict[str, int] = {}

BRIEF = """# Q3 Operations Review
## Highlights
- Uptime held at 99.95 percent
- Ticket backlog down 18 percent
## Regional revenue
| Region | Revenue (k USD) |
|---|---|
| North | 120 |
| South | 95 |
| East | 143 |
| West | 88 |
## Risks
- Vendor contract renewal in November
- Two open hiring requisitions
## Next steps
- Hire two site reliability engineers
- Close the vendor renewal by October 31
"""
DECK_TITLES = ["Q3 Operations Review", "Highlights", "Regional revenue", "Risks", "Next steps"]
REPORT_PAGES = (
    ("1. Summary", "Revenue grew 12 percent to 446 thousand USD in the third quarter."),
    ("2. Incidents", "Four incidents were declared; the mean time to recover was 38 minutes."),
    ("3. Outlook", "The fourth quarter plan holds headcount flat and targets 99.95 percent uptime."),
)
APPENDIX = ("Appendix A: Data sources", "Figures come from the finance export of October 2.")
FORM_VALUES = {"reviewer": "Dana Reyes", "approved_total": "446"}


def _pinned(engine: str) -> EngineVersion:
    return next(r.version for r in release_allowlist() if r.version.kind == engine)


def inputs() -> dict[str, bytes]:
    """The task's files under ``in/``, made here with reportlab (the test's own copy, not the sandbox's)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    appendix, form = io.BytesIO(), io.BytesIO()
    page = canvas.Canvas(appendix, pagesize=letter)
    page.setFont("Helvetica-Bold", 16)
    page.drawString(72, 700, APPENDIX[0])
    page.setFont("Helvetica", 11)
    page.drawString(72, 670, APPENDIX[1])
    page.save()
    sheet = canvas.Canvas(form, pagesize=letter)
    sheet.drawString(72, 720, "Report approval")
    sheet.drawString(72, 680, "Reviewer")
    sheet.acroForm.textfield(name="reviewer", tooltip="Reviewer name", x=180, y=672, width=240, height=20)  # pyright: ignore[reportUnknownMemberType]
    sheet.drawString(72, 640, "Approved total (k USD)")
    sheet.acroForm.textfield(name="approved_total", tooltip="Approved total", x=180, y=632, width=240, height=20)  # pyright: ignore[reportUnknownMemberType]
    sheet.save()
    return {"in/brief.md": BRIEF.encode(), "in/appendix.pdf": appendix.getvalue(), "in/form.pdf": form.getvalue()}


# The scripted model's programs. Each one does what the skill's SKILL.md (or forms.md) tells the engine to do,
# with the libraries the skill names; the skill's own scripts do the checks and the form work.
MAKE_DECK = '''import sys
from pptx import Presentation
from pptx.util import Inches

src, out = sys.argv[1], sys.argv[2]
title, sections = "", []
for line in open(src, encoding="utf-8"):
    line = line.rstrip("\\n")
    if line.startswith("# "):
        title = line[2:]
    elif line.startswith("## "):
        sections.append({"title": line[3:], "bullets": [], "rows": []})
    elif line.startswith("- "):
        sections[-1]["bullets"].append(line[2:])
    elif line.startswith("|") and not set(line) <= set("|- "):
        sections[-1]["rows"].append([cell.strip() for cell in line.strip("|").split("|")])
deck = Presentation()
first = deck.slides.add_slide(deck.slide_layouts[0])
first.shapes.title.text = title
first.placeholders[1].text = "Prepared from the brief"
for section in sections:
    if section["rows"]:
        slide = deck.slides.add_slide(deck.slide_layouts[5])
        slide.shapes.title.text = section["title"]
        rows = section["rows"]
        shape = slide.shapes.add_table(len(rows), len(rows[0]), Inches(1), Inches(1.8), Inches(8), Inches(0.4 * len(rows)))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                shape.table.cell(r, c).text = value
    else:
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = section["title"]
        body = slide.placeholders[1].text_frame
        body.text = section["bullets"][0]
        for bullet in section["bullets"][1:]:
            body.add_paragraph().text = bullet
deck.save(out)
print(f"wrote {out} with {len(deck.slides)} slides")
'''
EDIT_DECK = '''import sys
import zipfile
from pathlib import Path

import defusedxml.minidom

root, out = Path(sys.argv[1]), sys.argv[2]


def edit(part, old, new):
    path = root / "ppt" / "slides" / part
    document = defusedxml.minidom.parse(str(path))
    hits = [n for n in document.getElementsByTagName("a:t") if n.firstChild is not None and n.firstChild.nodeValue == old]
    assert len(hits) == 1, (part, old, len(hits))
    hits[0].firstChild.nodeValue = new
    path.write_bytes(document.toxml(encoding="UTF-8"))


edit("slide3.xml", "143", "151")
edit("slide6.xml", "Next steps", "Appendix: method")
edit("slide6.xml", "Hire two site reliability engineers", "Figures from the finance export of October 2")
names = sorted((p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()), key=lambda n: (n != "[Content_Types].xml", n))
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
    for name in names:
        archive.write(root / name, name)
print(f"wrote {out}")
'''
MAKE_REPORT = '''import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

PAGES = %r
styles = getSampleStyleSheet()
story = [Paragraph("Q3 Operations Report", styles["Title"]), Spacer(1, 12)]
for index, (heading, body) in enumerate(PAGES):
    if index:
        story.append(PageBreak())
    story += [Paragraph(heading, styles["Heading1"]), Paragraph(body, styles["Normal"])]
SimpleDocTemplate(sys.argv[1], pagesize=letter).build(story)
''' % (REPORT_PAGES,)
MERGE = '''import sys
from pypdf import PdfReader, PdfWriter

*sources, out = sys.argv[1:]
writer = PdfWriter()
for pdf_file in sources:
    for page in PdfReader(pdf_file).pages:
        writer.add_page(page)
with open(out, "wb") as output:
    writer.write(output)
'''
EXTRACT = '''import sys
from pypdf import PdfReader

reader = PdfReader(sys.argv[1])
text = ""
for page in reader.pages:
    text += page.extract_text() + "\\n"
open(sys.argv[2], "w", encoding="utf-8").write(text)
'''
FIELD_VALUES = json.dumps(
    [
        {"field_id": "reviewer", "description": "Reviewer name", "page": 1, "value": FORM_VALUES["reviewer"]},
        {"field_id": "approved_total", "description": "Approved total", "page": 1, "value": FORM_VALUES["approved_total"]},
    ]
)


def _drop(path: str, content: str) -> str:
    return f"echo {base64.b64encode(content.encode()).decode()} | base64 -d > {path}"


def read_skill(engine: str, skill: str) -> str:
    """opencode loads a skill with its skill tool; Codex lists skills and the model reads ``SKILL.md`` itself."""
    if engine == "opencode":
        return f"skill {skill}"
    return f"shell cat {PLACEMENTS[engine].dest}/{skill}/SKILL.md"


def pptx_prompts(engine: str) -> tuple[str, str]:
    sk = PLACEMENTS[engine].dest
    create = "\n".join(
        [
            "SK1 PPTX create: a 5-slide deck from in/brief.md.",
            read_skill(engine, "pptx"),
            f"shell mkdir -p out work && {_drop('work/make_deck.py', MAKE_DECK)} && python3 work/make_deck.py in/brief.md out/deck.pptx",
            f"shell python3 {sk}/pptx/scripts/office/validate.py out/deck.pptx",
            "say deck ready",
        ]
    )
    update = "\n".join(
        [
            "SK1 PPTX update: add an appendix slide and change East's revenue to 151.",
            read_skill(engine, "pptx"),
            "shell mkdir -p work && cp out/deck.pptx work/deck-v1.pptx && "
            "python3 -c \"import zipfile; zipfile.ZipFile('out/deck.pptx').extractall('work/deck')\"",
            f"shell python3 {sk}/pptx/scripts/add_slide.py work/deck slide5.xml",
            f"shell python3 {sk}/pptx/scripts/clean.py work/deck",
            f"shell {_drop('work/edit_deck.py', EDIT_DECK)} && python3 work/edit_deck.py work/deck out/deck.pptx",
            f"shell python3 {sk}/pptx/scripts/office/validate.py out/deck.pptx --original work/deck-v1.pptx",
            "say deck updated",
        ]
    )
    return create, update


def pdf_prompts(engine: str) -> tuple[str, str]:
    sk = PLACEMENTS[engine].dest
    create = "\n".join(
        [
            "SK1 PDF create: a 3-page report.",
            read_skill(engine, "pdf"),
            f"shell mkdir -p out work && {_drop('work/make_report.py', MAKE_REPORT)} && python3 work/make_report.py out/report.pdf",
            "shell python3 -c \"from pypdf import PdfReader; print(len(PdfReader('out/report.pdf').pages))\"",
            "say report ready",
        ]
    )
    update = "\n".join(
        [
            "SK1 PDF update: append the appendix, fill the approval form, extract the text.",
            read_skill(engine, "pdf"),
            f"shell cat {sk}/pdf/forms.md",
            f"shell mkdir -p work && cp out/report.pdf work/report-v1.pdf && {_drop('work/merge.py', MERGE)} "
            "&& python3 work/merge.py work/report-v1.pdf in/appendix.pdf out/report.pdf",
            f"shell cd {sk}/pdf && python3 scripts/check_fillable_fields.py /workspace/in/form.pdf",
            f"shell cd {sk}/pdf && python3 scripts/extract_form_field_info.py /workspace/in/form.pdf /workspace/work/fields.json",
            f"shell {_drop('work/field_values.json', FIELD_VALUES)} && cd {sk}/pdf && python3 scripts/fill_fillable_fields.py "
            "/workspace/in/form.pdf /workspace/work/field_values.json /workspace/out/form-filled.pdf",
            f"shell {_drop('work/extract.py', EXTRACT)} && python3 work/extract.py out/report.pdf out/report.txt",
            "say report updated",
        ]
    )
    return create, update


@dataclass
class SkillLane:
    """One run of one engine on the real lane: two turns (create, then a follow-up that updates)."""

    engine: str
    stack: RealStack
    broker: PlacingBroker
    models: ModelGatewayLane | None = None
    bind_run: Callable[[str, str], None] | None = None
    run_id: str = field(default_factory=lambda: f"sk1-{uuid.uuid4().hex[:8]}")
    clock: ManualClock = field(default_factory=ManualClock)
    raw: InMemoryBinaryObjectStore = field(default_factory=InMemoryBinaryObjectStore)
    keys: InMemoryKeyProvider = field(default_factory=InMemoryKeyProvider)
    sink: InMemoryEngineEventSink = field(default_factory=InMemoryEngineEventSink)
    artifacts: InMemoryArtifactStore = field(default_factory=InMemoryArtifactStore)
    publications: InMemoryPublicationLog = field(default_factory=InMemoryPublicationLog)
    gateways: dict[int, EndpointConformanceGateway] = field(default_factory=dict[int, EndpointConformanceGateway])
    access: AccessRouteRef = field(default_factory=lambda: AccessRouteRef("route-sk1", "d1"))
    routes_digest: str | None = None

    def __post_init__(self) -> None:
        self.route = PinnedRoute("openai", f"scripted-{self.run_id}", config_model="gpt-4o")
        self.objects = SealedObjectStore(self.raw, self.keys)
        self.store = InMemoryWorkspaceStore(clock=self.clock)
        # No skill pins: the user's skills reach the sandbox through PlacingBroker, not P7.1 bundles.
        source = InMemorySkillContentSource()
        self.controller = EngineController(
            registry=release_registry(),
            broker=self.broker,  # type: ignore[arg-type] - PlacingBroker delegates the rest of the port
            workspaces=self.store,
            sync_for=lambda scope: TarWorkspaceSync(objects=self.objects, scope=scope, clock=self.clock),
            strategies=SyncStrategies(),
            skill_bundles=StoredSkillBundleBuilder(source=source, objects=self.objects, workspaces=self.store),
            skill_materializer=TarSkillMaterializer(objects=self.objects, workspaces=self.store),
            checkpoints=ObjectStoreEngineCheckpoints(self.objects),
            sink=self.sink,
            tool_host=HttpToolGatewayHost(self.stack.gateway.app, base_url=self.stack.gateway_base),
            clock=self.clock,
            placeholder_key=b"sk1-lane-placeholder-key-0123456789ab",
            writers=InMemoryWorkspaceWriters(clock=self.clock),
            config=EngineControllerConfig(
                images={_pinned(self.engine): IMAGES[self.engine]},
                resources=Resources(cpus=2.0, memory_mb=2048, pids=256, scratch_mb=512),
            ),
        )

    @property
    def scope(self) -> ObjectScope:
        return ObjectScope(TENANT, f"ws:{SESSION}")

    @property
    def binding(self) -> RunBinding:
        return RunBinding(TENANT, SESSION, self.run_id)

    def gateway(self, attempt: int) -> EndpointConformanceGateway:
        if attempt not in self.gateways:
            gateway = EndpointConformanceGateway(
                endpoints=EngineControllerConfig(images={}).endpoints("x" * 16, "y" * 16),
                grant=GRANT,
                binding=self.binding,
                attempt=attempt,
                routes_digest=self.routes_digest or route_digest((self.route,)),
            )
            if self.gateways:
                first = next(iter(self.gateways.values()))
                gateway.brain = first.brain
                gateway.model_calls = first.model_calls
                self.stack.models.gateways[MODEL_KEY] = gateway
            elif self.models is not None:
                self.models.pin(gateway, self.route, key=MODEL_KEY)
            self.gateways[attempt] = gateway
            attempt_id = f"{self.run_id}.a{attempt}"
            self.stack.models.bind(self.run_id, attempt_id, gateway.claims.binding)
            if self.bind_run is not None:
                self.bind_run(self.run_id, attempt_id)
        return self.gateways[attempt]

    def context(self, attempt: int, **changes: object) -> EngineRunContext:
        gateway = self.gateway(attempt)
        context = EngineRunContext(
            tenant=TENANT,
            session_id=SESSION,
            run_id=self.run_id,
            attempt_id=f"{self.run_id}.a{attempt}",
            fence=attempt,
            lease_holder="worker-sk1",
            profile=release_default_profile(_pinned(self.engine), self.access),
            tools=gateway.endpoint,
            tool_binding=gateway.claims.binding,
            grant_size=len(GRANT),
            artifacts=WorkspaceArtifactPublisher(
                self.artifacts, self.publications, self.objects, object_scope=lambda b: ObjectScope(b.tenant, f"ws:{b.scope}")
            ),
            labels={**OWNER, "pipeshub.agent": "SK1", "pxh.lane": self.stack.suffix},
        )
        return dataclasses.replace(context, **changes)  # type: ignore[arg-type]

    async def seed(self, files: Mapping[str, bytes]) -> None:
        entries = [WorkspaceFile(path, await self.objects.put(self.scope, data)) for path, data in files.items()]
        await self.store.commit(ObjectScope(TENANT), f"ws:{SESSION}", base_revision=0, changes=ChangeSet.of(*entries), reason="seed")

    async def two_turns(self, create: str, update: str) -> tuple[EngineTurnOutcome, dict[str, bytes], EngineTurnOutcome]:
        """Turn 1, the artifacts as published then, and turn 2 (a follow-up restored from turn 1's checkpoint)."""
        first = await self.controller.run_turn(self.context(1), EngineTask(turn_id=turn_id_for(self.run_id, 0), prompt=create))
        after_first = await self.published()
        second = await self.controller.run_turn(
            self.context(2, restore=first.checkpoint), FollowUp(turn_id_for(self.run_id, 1), update)
        )
        return first, after_first, second

    async def published(self) -> dict[str, bytes]:
        """name@version → bytes for every version of every artifact the run published."""
        out: dict[str, bytes] = {}
        for stored in self.artifacts.listing(self.binding):
            for version in range(1, stored.version + 1):
                out[f"{stored.name}@{version}"] = await self.artifacts.content(self.binding, stored.artifact_id, version)
        return out

    def events(self) -> list[EngineEvent]:
        return self.sink.of_run(self.run_id)

    def commands(self) -> list[tuple[str, int | None]]:
        """Each shell command the engine ran, with its exit code."""
        return [(command, code) for command, code, _ in self._commands()]

    def _commands(self) -> list[tuple[str, int | None, str]]:
        # Codex names a command after the model's call id, which a scripted model reuses in every turn.
        def key(event: CommandStarted | CommandFinished | CommandOutput) -> tuple[str, str]:
            return (event.meta.turn_id, event.call_id)

        out: dict[tuple[str, str], str] = {}
        for event in self.events():
            if isinstance(event, CommandOutput):
                out[key(event)] = out.get(key(event), "") + event.text
        finished = {key(e): e.exit_code for e in self.events() if isinstance(e, CommandFinished)}
        return [
            (e.command, finished.get(key(e)), out.get(key(e), ""))
            for e in self.events()
            if isinstance(e, CommandStarted)
        ]

    def transcript(self) -> str:
        """Each command with its exit code and the tail of its output: the failure message of a lane check."""
        return "\n".join(f"$ {c[:160]} -> {code}\n{out[-600:]}" for c, code, out in self._commands())

    def tools(self) -> list[str]:
        return [e.tool for e in self.events() if isinstance(e, ToolCallSeen) and e.status == "completed"]


# Checks, shared with the live lane (test_live_skill_packs.py): each returns the failed checks' names.


def check_deck(published: Mapping[str, bytes], after_first: Mapping[str, bytes]) -> list[str]:
    failed: list[str] = []
    v1, v2 = published.get("deck.pptx@1"), published.get("deck.pptx@2")
    if v1 is None or v2 is None:
        return [f"versions {sorted(k for k in published if k.startswith('deck.pptx'))}"]
    if after_first.get("deck.pptx@1") != v1:
        failed.append("v1 changed after the update")
    first, second = slide_texts(v1), slide_texts(v2)
    if len(first) != 5:
        failed.append(f"v1 has {len(first)} slides")
    # Shapes and line breaks are the engine's choice: each title among its slide's words.
    if len(first) != len(DECK_TITLES) or not all(
        words(title) in words(" ".join(texts)) for title, texts in zip(DECK_TITLES, first, strict=True)
    ):
        failed.append("v1 titles")
    if not {"North", "120", "South", "95", "East", "143", "West", "88"} <= set(first[2] if len(first) > 2 else []):
        failed.append("v1 table values")
    if len(second) != 6 or len(first) != 5:
        failed.append(f"v2 has {len(second)} slides")
        return failed
    failed.extend(f"slide {i + 1} text changed" for i in (0, 1, 3, 4) if first[i] != second[i])
    # Every region keeps its value but East, which reads 151 wherever the slide shows it; figures derived from it
    # (a total an engine drew on the slide) may follow, which the task does not forbid.
    table = set(second[2])
    if not {"Regional revenue", "North", "120", "South", "95", "East", "151", "West", "88"} <= table or any(
        "143" in text for text in second[2]
    ):
        failed.append("slide 3 is not the old table with East = 151")
    if not any("Appendix" in text for text in second[5]):
        failed.append("slide 6 is not the appendix")
    return failed


def check_deck_parts(published: Mapping[str, bytes]) -> list[str]:
    """Scripted lane only: the slide parts the update did not touch are byte-identical."""
    v1, v2 = pptx_parts(published["deck.pptx@1"]), pptx_parts(published["deck.pptx@2"])
    return [f"{part} bytes changed" for part in ("ppt/slides/slide1.xml", "ppt/slides/slide2.xml", "ppt/slides/slide4.xml", "ppt/slides/slide5.xml") if v1.get(part) != v2.get(part)]


def check_report(published: Mapping[str, bytes], after_first: Mapping[str, bytes]) -> list[str]:
    failed: list[str] = []
    v1, v2 = published.get("report.pdf@1"), published.get("report.pdf@2")
    if v1 is None or v2 is None:
        return [f"versions {sorted(k for k in published if k.startswith('report.pdf'))}"]
    if after_first.get("report.pdf@1") != v1:
        failed.append("v1 changed after the update")
    first, second = pdf_pages(v1), pdf_pages(v2)
    if len(first) != 3:
        failed.append(f"v1 has {len(first)} pages")
    for index, (heading, body) in enumerate(REPORT_PAGES):
        page = words(first[index]) if index < len(first) else ""
        if heading not in page or words(body) not in page:
            failed.append(f"v1 page {index + 1} text")
    if len(second) != 4:
        failed.append(f"v2 has {len(second)} pages")
        return failed
    if [words(p) for p in second[:3]] != [words(p) for p in first[:3]]:
        failed.append("pages 1-3 text changed")
    if not all(part in words(second[3]) for part in APPENDIX):
        failed.append("page 4 is not the appendix")
    form = published.get("form-filled.pdf@1")
    if form is None or pdf_form_values(form) != FORM_VALUES:
        failed.append(f"form values {pdf_form_values(form) if form else None}")
    text = published.get("report.txt@1", b"").decode("utf-8", "replace")
    if not all(word in words(text) for word in (*(h for h, _ in REPORT_PAGES), APPENDIX[0], "446 thousand USD")):
        failed.append("report.txt")
    return failed


def skill_reads(lane: SkillLane, skill: str) -> list[str]:
    """How the engine read the skill: opencode's skill tool calls, or commands that read its SKILL.md."""
    path = f"{PLACEMENTS[lane.engine].dest}/{skill}/SKILL.md"
    reads = [command for command, _ in lane.commands() if path in command]
    return reads + [tool for tool in lane.tools() if tool == "skill"]


def skill_scripts_run(lane: SkillLane, skill: str) -> list[tuple[str, int | None]]:
    """Commands that ran one of the skill's own scripts, by its path relative to the skill folder: engines run
    them from the skill folder as a working directory (Codex's ``workdir``, a ``cd``) or by absolute path."""
    scripts = {p.split("/", 1)[1] for p in manifest()["files"] if p.startswith(f"{skill}/scripts/") and p.endswith(".py")}
    return [(c, code) for c, code in lane.commands() if any(s in c for s in scripts)]


@pytest.fixture(scope="module")
def skills_tree() -> dict[str, dict[str, bytes]]:
    trees: dict[str, dict[str, bytes]] = {}
    for engine in ENGINES:
        root = installed_dir(engine)
        if root is None:
            pytest.skip("SK1_SKILLS_DIR names no installed skills (the skills are not in this repository)")
        assert tree_mismatch(root) == [], "the installed skills are not the pinned ones (IN-2)"
        trees[engine] = read_tree(root)
    return trees


@pytest.fixture(scope="module")
def real_stack() -> Iterator[RealStack]:
    for image in IMAGES.values():
        available, reason = lane_available(image)
        if not available:
            pytest.skip(f"{reason} (deployment/sandbox-images/skill-runtime/build.sh)")

    def upstreams(stack: RealStack) -> Mapping[str, str]:
        _E3_PORT["port"] = _free_port(stack.host_ip)
        return {"EGRESS_UPSTREAMS": json.dumps({"model-gateway": f"http://{stack.host_ip}:{_E3_PORT['port']}"})}

    stack = start_stack(upstreams=upstreams)
    yield stack
    stop_stack(stack)


@pytest.fixture
async def model_gateway(stack: RealStack) -> AsyncIterator[ModelGatewayLane]:
    async with ModelGatewayLane(stack, _E3_PORT["port"]).serving() as lane:
        yield lane


@contextlib.asynccontextmanager
async def placing_broker(stack: RealStack, engine: str, tree: Mapping[str, bytes]) -> AsyncIterator[PlacingBroker]:
    namespace = f"io.pipeshub.sk1.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-sk1-" + namespace.rsplit(".", 1)[1]
    inner, provider = await engine_broker(stack, namespace, prefix, image=IMAGES[engine])
    try:
        yield PlacingBroker(inner, PLACEMENTS[engine], tree)
    finally:
        await inner.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
        provider.close()


def assert_placed(broker: PlacingBroker) -> None:
    """PL-1/PL-2: inside every sandbox of the run, the engine's path holds exactly the installed files."""
    pinned = {path: entry["sha256"] for path, entry in manifest()["files"].items()}
    assert len(broker.inspections) == 2
    for seen in broker.inspections:
        assert seen.files == tuple(sorted(pinned))
        assert dict(seen.digests) == pinned
        assert {"pdf/SKILL.md", "pdf/scripts/fill_fillable_fields.py", "pptx/SKILL.md", "pptx/scripts/office/validate.py"} <= set(seen.files)
        if broker.placement.read_only:
            assert seen.write_exit != 0 and "Read-only file system" in seen.write_error, seen
        else:
            assert seen.write_exit == 0, seen


def listed_paths(body: str) -> dict[str, str]:
    """Skill name → the ``SKILL.md`` path the engine showed the model in one request's text.

    opencode writes ``<location>`` per skill; Codex 0.160 names a root once (`` `r0` = `/path` ``) and each
    skill's file under it (``(file: r0/<name>/SKILL.md)``)."""
    found: dict[str, str] = {}
    roots = dict(re.findall(r"`(r\d+)` = `([^`]+)`", body))
    for root, name in re.findall(r"\(file: (r\d+)/([^/]+)/SKILL\.md\)", body):
        found[name] = f"{roots.get(root, root)}/{name}/SKILL.md"
    for location in re.findall(r"<location>([^<]+)</location>", body):
        path = location.removeprefix("file://")
        found[path.rstrip("/").rsplit("/", 2)[-2]] = path
    return found


def assert_listed(engine: str, model_gateway: ModelGatewayLane) -> str:
    """DS-1/DS-2: the engine put both skills, with their descriptions and paths, in front of the model.

    Returns the listing as the model saw it (for the evidence table)."""
    texts = [_all_text(r.body) for r in model_gateway.requests]
    assert texts
    first = texts[0]
    dest = PLACEMENTS[engine].dest
    for description in (
        "Use this skill whenever the user wants to do anything with PDF files",
        "Use this skill any time a .pptx or .potx file is involved",
    ):
        assert description in first, (engine, description)
    print(f"\nSK1 {engine} listing: {first[first.find('skill'):][:200]!r}")
    assert listed_paths(first) == {"pdf": f"{dest}/pdf/SKILL.md", "pptx": f"{dest}/pptx/SKILL.md"}
    for bundled in ("imagegen", "openai-docs", "skill-installer", "skill-creator"):
        assert f"- {bundled}:" not in first and f"<name>{bundled}</name>" not in first
    start = min(i for i in (first.find("<available_skills>"), first.find("### Skill roots")) if i >= 0)
    return first[start : start + 400]


def _all_text(value: JsonValue) -> str:
    """Every string inside a request body, joined: a body's prompt text as the model reads it."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_all_text(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(_all_text(v) for v in value)
    return ""


def assert_skill_loaded(lane: SkillLane, model_gateway: ModelGatewayLane, skill: str, line: str) -> None:
    """DS-3/DS-4 and UV-5: the engine read the skill and the next model call held its instructions."""
    assert skill_reads(lane, skill), lane.commands()
    assert any(line in json.dumps(r.body) for r in model_gateway.requests), line


def assert_no_installs_or_egress(lane: SkillLane, broker: PlacingBroker) -> None:
    """UV-6."""
    commands = " ".join(c for c, _ in lane.commands())
    assert "pip install" not in commands and "npm install" not in commands
    for seen in broker.inspections:
        assert lane.stack.refusals_for(seen.sandbox_id) == []


@pytest.mark.parametrize("engine", ENGINES)
async def test_pptx_create_then_update_with_the_skill(
    engine: str, stack: RealStack, model_gateway: ModelGatewayLane, skills_tree: dict[str, dict[str, bytes]]
) -> None:
    async with placing_broker(stack, engine, skills_tree[engine]) as broker:
        lane = SkillLane(engine, stack, broker, models=model_gateway)
        await lane.seed(inputs())
        first, after_first, second = await lane.two_turns(*pptx_prompts(engine))
    assert (first.reason, first.final_text.strip()) == ("completed", "deck ready")
    assert (second.reason, second.final_text.strip(), second.restored) == ("completed", "deck updated", "state")
    assert_placed(broker)
    assert_listed(engine, model_gateway)
    assert_skill_loaded(lane, model_gateway, "pptx", "# PPTX creation, editing, and analysis")
    scripts = skill_scripts_run(lane, "pptx")
    assert [code for _, code in scripts] == [0, 0, 0, 0], lane.transcript()
    assert {s.rsplit("/scripts/", 1)[1].split()[0] for s, _ in scripts} == {"office/validate.py", "add_slide.py", "clean.py"}
    published = await lane.published()
    assert check_deck(published, after_first) == []
    assert check_deck_parts(published) == []
    assert_no_installs_or_egress(lane, broker)
    print(f"\nSK1 {engine} pptx: commands={len(lane.commands())} tools={sorted(set(lane.tools()))} "
          f"versions={sorted(published)} requests={len(model_gateway.requests)}")


@pytest.mark.parametrize("engine", ENGINES)
async def test_pdf_create_then_update_with_the_skill(
    engine: str, stack: RealStack, model_gateway: ModelGatewayLane, skills_tree: dict[str, dict[str, bytes]]
) -> None:
    async with placing_broker(stack, engine, skills_tree[engine]) as broker:
        lane = SkillLane(engine, stack, broker, models=model_gateway)
        await lane.seed(inputs())
        first, after_first, second = await lane.two_turns(*pdf_prompts(engine))
    assert (first.reason, first.final_text.strip()) == ("completed", "report ready")
    assert (second.reason, second.final_text.strip(), second.restored) == ("completed", "report updated", "state")
    assert_placed(broker)
    assert_listed(engine, model_gateway)
    assert_skill_loaded(lane, model_gateway, "pdf", "# PDF Processing Guide")
    assert any(f"{PLACEMENTS[engine].dest}/pdf/forms.md" in c for c, _ in lane.commands())
    scripts = skill_scripts_run(lane, "pdf")
    assert [code for _, code in scripts] == [0, 0, 0], lane.transcript()
    published = await lane.published()
    assert check_report(published, after_first) == []
    assert_no_installs_or_egress(lane, broker)
    print(f"\nSK1 {engine} pdf: commands={len(lane.commands())} tools={sorted(set(lane.tools()))} "
          f"versions={sorted(published)} requests={len(model_gateway.requests)}")
