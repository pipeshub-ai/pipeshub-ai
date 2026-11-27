"""The scripted model's part on the offline lane: each case's reference
solution as a :class:`ModelScript`.

The script only decides what the model *says*: tool calls and the final
text. Everything else is real: ``run_code`` runs the reference program in the
sandbox, files come back through the artifact registry, knowledge comes back
from the product's search tool, and the final answer's ``[source](refN)``
links are turned into citations by the product. The last turn is computed
from the tool results it received (``from_request``), so a broken tool path
shows up as a failed case rather than a canned answer.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, cast

from harness_core.testing import ModelScript
from harness_core.testing.model_script import Reply, ToolCallSpec, Turn, Usage
from tests.support.parity_cases.wave1_cases import load_knowledge, parse_answer_lines

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.testing.model_types import ModelRequest
    from tests.support.parity_cases.wave1_cases import ReferenceDocument, Wave1Case

SEARCH_TOOL = "knowledgegraph__search"
CODE_TOOL = "run_code"

# Token counts a scripted turn reports, so the offline lane exercises the
# usage and cost plumbing (the scripted model itself is unpriced).
USAGE = Usage(input_tokens=1500, output_tokens=80)

_RECORD_REF = re.compile(r"Record: (?P<title>[^\n]+)\nRecord blocks \(sorted\):\n\[\d+\|(?P<ref>ref\d+)\]")
_FACT_LINE = re.compile(r"^FACT\s+([\w.-]+)\s*=\s*(.+?)\s*$", re.MULTILINE)


def _searches(case: Wave1Case) -> Turn:
    calls = [
        ToolCallSpec(id=f"s{i + 1}", name=SEARCH_TOOL, args={"query": q})
        for i, q in enumerate(case.reference.searches)
    ]
    return Turn(reply=Reply(tool_calls=calls, usage=USAGE))


def _stdout(request: ModelRequest) -> str:
    last = request.last("tool")
    text = last.text if last is not None else ""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text.replace("\\n", "\n")
    if not isinstance(data, dict):
        return text
    return str(cast("dict[str, object]", data).get("stdout", ""))


def _data_answer(request: ModelRequest) -> Reply:
    stdout = _stdout(request)
    lines = [f"- {k}: {v}" for k, v in _FACT_LINE.findall(stdout)]
    lines += [f"- {k}: {_plain(v)}" for k, v in parse_answer_lines(stdout).items()]
    if not lines:
        return Reply(text="The program printed no result, so I cannot answer.", usage=USAGE)
    return Reply(text="Here is what the data shows:\n" + "\n".join(lines), usage=USAGE)


def _plain(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(value)


def render_document(doc: ReferenceDocument) -> str:
    """A self-contained program that writes ``doc`` to ``$OUTPUT_DIR`` with
    the libraries the sandbox image ships (python-docx, python-pptx, openpyxl)."""
    head = "import os\nOUT = os.path.join(os.environ['OUTPUT_DIR'], %r)\n" % doc.name
    if doc.sections:
        body = [(s.heading, s.text) for s in doc.sections]
        return head + (
            "from docx import Document\n"
            "d = Document()\n"
            f"for heading, text in {body!r}:\n"
            "    d.add_heading(heading, level=1)\n"
            "    d.add_paragraph(text)\n"
            "d.save(OUT)\n"
        )
    if doc.slides:
        slides = [(s.title, list(s.bullets)) for s in doc.slides]
        return head + (
            "from pptx import Presentation\n"
            "prs = Presentation()\n"
            f"for i, (title, bullets) in enumerate({slides!r}):\n"
            "    slide = prs.slides.add_slide(prs.slide_layouts[0 if i == 0 else 1])\n"
            "    slide.shapes.title.text = title\n"
            "    if bullets:\n"
            "        frame = slide.placeholders[1].text_frame\n"
            "        frame.text = bullets[0]\n"
            "        for b in bullets[1:]:\n"
            "            frame.add_paragraph().text = b\n"
            "prs.save(OUT)\n"
        )
    assert doc.table is not None
    table = doc.table
    return head + (
        "from openpyxl import Workbook\n"
        "wb = Workbook()\n"
        "ws = wb.active\n"
        f"ws.title = {table.sheet!r}\n"
        f"header = {table.header!r}\n"
        f"rows = {table.rows!r}\n"
        "ws.append(header)\n"
        "for row in rows:\n"
        "    ws.append(row)\n"
        f"total_column = {table.total_column!r}\n"
        "if total_column:\n"
        "    col = header.index(total_column)\n"
        "    total = ['Total'] + [None] * (len(header) - 1)\n"
        "    total[col] = sum(r[col] for r in rows)\n"
        "    ws.append(total)\n"
        "wb.save(OUT)\n"
    )


def refs_by_doc(request: ModelRequest, titles: Mapping[str, str] | None = None) -> dict[str, str]:
    """Fixture doc id -> the ``refN`` the product gave it in search results; ``titles`` (title -> doc id) names the
    fixture's docs (default: the wave 1 knowledge)."""
    titles = titles if titles is not None else {d.title: d.id for d in load_knowledge()}
    found: dict[str, str] = {}
    for message in request.messages:
        if message.role != "tool" or message.tool_name != SEARCH_TOOL:
            continue
        for match in _RECORD_REF.finditer(message.text):
            doc = titles.get(match.group("title").strip())
            if doc is not None:
                found.setdefault(doc, match.group("ref"))
    return found


def _report_answer(case: Wave1Case) -> Any:  # noqa: ANN401 - Turn.from_request callable
    def build(request: ModelRequest) -> Reply:
        refs = refs_by_doc(request)
        parts: list[str] = []
        for sentence in case.reference.answer:
            links = [f"[source]({refs[d]})" for d in sentence.cite if d in refs]
            parts.append(" ".join([sentence.text, *links]))
        return Reply(text="\n\n".join(parts), usage=USAGE)

    return build


def script_for(case: Wave1Case) -> ModelScript:
    ref = case.reference
    turns: list[Turn] = []
    if case.suite == "data_analysis":
        assert ref.code is not None
        turns.append(
            Turn(
                reply=Reply(
                    tool_calls=[
                        ToolCallSpec(
                            id="c1",
                            name=CODE_TOOL,
                            args={"language": "python", "code": ref.code, "input_artifacts": list(case.inputs)},
                        )
                    ],
                    usage=USAGE,
                )
            )
        )
        turns.append(Turn(from_request=_data_answer))
    elif case.suite == "office_documents":
        assert ref.document is not None
        turns.append(_searches(case))
        turns.append(
            Turn(
                reply=Reply(
                    tool_calls=[
                        ToolCallSpec(
                            id="c1",
                            name=CODE_TOOL,
                            args={"language": "python", "code": render_document(ref.document)},
                        )
                    ],
                    usage=USAGE,
                )
            )
        )
        turns.append(
            Turn(reply=Reply(text=f"I created {ref.document.name} from the knowledge base.", usage=USAGE))
        )
    else:
        turns.append(_searches(case))
        turns.append(Turn(from_request=_report_answer(case)))
    return ModelScript(id=case.id, turns=turns)


__all__ = ["CODE_TOOL", "SEARCH_TOOL", "USAGE", "refs_by_doc", "render_document", "script_for"]
