"""The indexing pipeline's classification calls, through the app's own Ollama client, against the fake model.

The simulator shards of the integration workflow run on integration-tests/fake_model/server.py instead of a paid
model. ChatOllama gets no structured output (``_apply_structured_output``), so the app sends its classification
prompt with no ``format`` and parses the reply text: a fake that answered plain text would fail extraction on every
record and fill the shard's logs with errors. This pins that the fake's answers parse as the app's own schemas on the
first attempt, so a reworded prompt or a changed schema fails here instead of on the shard.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.modules.entity_resolution.models import (
    TOPIC,
    ExtractedName,
    MergeDecisions,
    WinnerCandidate,
)
from app.modules.entity_resolution.prompt import build_prompt
from app.modules.extraction.prompt_template import (
    prompt_for_code_extraction,
    prompt_for_document_extraction,
)
from app.modules.named_entities.resolution import _SelectBatch
from app.modules.parsers.excel.prompt_template import (
    CSVHeaderDetection,
    ExcelHeaderDetection,
    KeyedRowDescriptions,
    TableEnrichment,
    TableHeaders,
    TableSummaryHeaders,
    csv_header_generation_prompt,
    excel_header_detection_prompt,
    excel_header_generation_prompt,
    keyed_row_text_prompt,
    table_enrichment_prompt,
    table_summary_headers_prompt,
)
from app.modules.transformers.document_extraction import (
    CodeClassification,
    DocumentClassification,
)
from app.utils.aimodels import LLMProvider, get_generator_model
from app.utils.indexing_helpers import TableSummary, table_summary_prompt_template
from app.utils.streaming import invoke_with_structured_output_and_reflection

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

    from langchain_core.language_models import BaseChatModel

FAKE_MODEL = Path(__file__).resolve().parents[5] / "integration-tests" / "fake_model" / "server.py"


def _fake_model_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fake_model_server", FAKE_MODEL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fake_model() -> Iterator[tuple[str, list[str]]]:
    """The fake model on a free port, and the answer kinds it gave, in order."""
    module = _fake_model_module()
    answered: list[str] = []

    class Recording(module.Handler):
        def _log(self, model: object, kind: str) -> None:
            answered.append(kind)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Recording)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", answered
    finally:
        server.shutdown()
        server.server_close()


def _llm(endpoint: str) -> BaseChatModel:
    config = {"configuration": {"model": "qwen2.5:0.5b", "endpoint": endpoint}}
    return get_generator_model(LLMProvider.OLLAMA.value, config)


@pytest.mark.asyncio
async def test_document_classification_parses_on_the_first_attempt(fake_model) -> None:
    endpoint, answered = fake_model
    answered.clear()
    instructions = prompt_for_document_extraction.replace("{department_list}", '     - "Engineering"')
    messages = [SystemMessage(content=instructions), HumanMessage(content="Record: notes.txt\nDocument Content: hello")]
    result = await invoke_with_structured_output_and_reflection(_llm(endpoint), messages, DocumentClassification)
    assert isinstance(result, DocumentClassification)
    assert answered == ["document"], answered  # one call: no reflection retry was needed


@pytest.mark.asyncio
async def test_code_classification_parses_on_the_first_attempt(fake_model) -> None:
    endpoint, answered = fake_model
    answered.clear()
    messages = [SystemMessage(content=prompt_for_code_extraction), HumanMessage(content="File: app.py\n\ndef f(): pass")]
    result = await invoke_with_structured_output_and_reflection(_llm(endpoint), messages, CodeClassification)
    assert isinstance(result, CodeClassification)
    assert answered == ["code"], answered


def _template_messages(prompt) -> list:
    """The template's messages with their placeholders left in: the fake matches the instructions, not the data."""
    kinds = {"system": SystemMessage, "human": HumanMessage}
    return [kinds[message.__class__.__name__.split("Message")[0].lower()](content=message.prompt.template)
            for message in prompt.messages]


@pytest.mark.asyncio
async def test_entity_merge_decisions_decide_every_item_on_the_first_attempt(fake_model) -> None:
    endpoint, answered = fake_model
    answered.clear()
    items = [
        ExtractedName(index=0, kind=TOPIC, raw="Payroll", display="Payroll", normalized="payroll"),
        ExtractedName(index=1, kind=TOPIC, raw="Refunds", display="Refunds", normalized="refunds"),
    ]
    candidates = {0: (WinnerCandidate(entity_id="e1", name="Invoices"),)}
    messages = [HumanMessage(content=build_prompt("A test document.", items, candidates))]
    result = await invoke_with_structured_output_and_reflection(_llm(endpoint), messages, MergeDecisions)
    assert isinstance(result, MergeDecisions)
    # An empty list would also parse; the resolver treats it as a model that answered nothing.
    assert sorted(decision.i for decision in result.decisions) == [0, 1]
    assert answered == ["merge"], answered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "schema", "kind"),
    [
        (table_enrichment_prompt, TableEnrichment, "table"),
        (table_summary_headers_prompt, TableSummaryHeaders, "table-summary"),
        (keyed_row_text_prompt, KeyedRowDescriptions, "rows"),
    ],
)
async def test_table_prompts_parse_on_the_first_attempt(fake_model, prompt, schema, kind) -> None:
    endpoint, answered = fake_model
    answered.clear()
    result = await invoke_with_structured_output_and_reflection(_llm(endpoint), _template_messages(prompt), schema)
    assert isinstance(result, schema)
    assert answered == [kind], answered


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [ExcelHeaderDetection, CSVHeaderDetection])
async def test_header_detection_parses_on_the_first_attempt(fake_model, schema) -> None:
    endpoint, answered = fake_model
    answered.clear()
    messages = excel_header_detection_prompt.format_messages(rows_text="Row 1: a, b\nRow 2: 1, 2")
    result = await invoke_with_structured_output_and_reflection(_llm(endpoint), messages, schema)
    assert isinstance(result, schema)
    assert answered == ["header-detection"], answered


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", [csv_header_generation_prompt, excel_header_generation_prompt])
async def test_generated_headers_have_the_number_asked_for(fake_model, prompt) -> None:
    endpoint, answered = fake_model
    answered.clear()
    messages = prompt.format_messages(sample_count=2, sample_data="[[1, 2, 3], [4, 5, 6]]", column_count=3)
    result = await invoke_with_structured_output_and_reflection(_llm(endpoint), messages, TableHeaders)
    assert isinstance(result, TableHeaders)
    assert len(result.headers) == 3
    assert answered == ["headers"], answered


@pytest.mark.asyncio
async def test_the_indexing_table_summary_parses_on_the_first_attempt(fake_model) -> None:
    endpoint, answered = fake_model
    answered.clear()
    prompt = table_summary_prompt_template.replace("{{table_data}}", "a,b\n1,2")
    result = await invoke_with_structured_output_and_reflection(_llm(endpoint), [HumanMessage(content=prompt)], TableSummary)
    assert isinstance(result, TableSummary)
    assert answered == ["table-summary-json"], answered


@pytest.mark.asyncio
async def test_named_entity_selection_answers_every_line(fake_model) -> None:
    endpoint, answered = fake_model
    answered.clear()
    # The prompt NamedEntityResolver._select builds (app/modules/named_entities/resolution.py).
    prompt = (
        "For each line pick a candidate id or NEW. "
        'Return JSON {"decisions": [{"name": "...", "id": "..."}]}.\n'
        "Acme | at Acme Corp | e1=Acme Inc\nBolt | Bolt shipped | e2=Bolt Ltd"
    )
    result = await invoke_with_structured_output_and_reflection(_llm(endpoint), [HumanMessage(content=prompt)], _SelectBatch)
    assert isinstance(result, _SelectBatch)
    assert {item.name for item in result.decisions} == {"Acme", "Bolt"}
    assert answered == ["entity-select"], answered
