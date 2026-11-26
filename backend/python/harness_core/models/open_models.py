"""Open models served by vLLM, pinned per model (C13.04): the server version, the chat template, the parsers.

An open model's tool calls and reasoning reach the OpenAI-compatible transport only through vLLM's parsers: the chat
template renders the tools and the history into the model's own format, the tool-call parser turns the model's text
(Qwen3-Coder's XML, Kimi K2's section tokens, GLM's ``<tool_call>`` blocks) back into ``tool_calls``, and the
reasoning parser moves the thinking into a separate field. A different vLLM, template or parser changes what the
transport receives, and the known failures are in exactly those pieces (research.md: Qwen3-Coder's XML format and
vLLM's parsers). So a deployment serves an open model only as a row here describes it, and each row has a
conformance case (``tests/unit/harness_core/models/test_c13_04_open_models.py``) run against the fake vLLM server
in ``harness_core.testing.fake_vllm``, which reproduces what that row's parsers put on the wire.

A row's template digest is the SHA-256 of the template text vLLM renders with. It is recorded from the rendered
template of a running server (``template_digest``), never typed in: a row whose digest is not recorded yet is
``verified = False`` and ``check_template`` refuses no template for it, but says it is unverified.

Each row cites the vLLM page its parser flags were read from.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final, Literal

# What the reasoning parser's field is called in a Chat Completions delta. vLLM names it ``reasoning_content``;
# later releases add ``reasoning``. The transport reads both (``REASONING_FIELDS``), so a row pins what its server
# sends and the conformance case checks that the transport reads it.
ReasoningField = Literal["reasoning_content", "reasoning"]
REASONING_FIELDS: Final[tuple[ReasoningField, ...]] = ("reasoning_content", "reasoning")

VLLM_VERSION: Final = "0.11.0"
VLLM_IMAGE: Final = f"vllm/vllm-openai:v{VLLM_VERSION}"

TemplateStatus = Literal["verified", "unverified", "mismatch"]


@dataclass(frozen=True, slots=True)
class ChatTemplate:
    """The template vLLM renders with: the model's own (``source="model"``, the file in its repository at the pinned
    revision) or one vLLM ships (``source="vllm"``, passed with ``--chat-template``)."""

    name: str
    source: Literal["model", "vllm"] = "model"
    # SHA-256 (hex) of the template text; ``None`` until recorded from a running server.
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class OpenModelPin:
    """How one open model is served: the model, the server and its parsers, and what the model accepts."""

    key: str
    # The Hugging Face repository; ``revision`` its commit, ``None`` until recorded with the template's digest.
    repository: str
    # Model ids an endpoint serves the model under (``--served-model-name`` or the repository's name).
    served: re.Pattern[str]
    template: ChatTemplate
    tool_parser: str
    reasoning_parser: str | None = None
    reasoning_field: ReasoningField = "reasoning_content"
    # A tool-call id is the model's own (Kimi K2: ``functions.<name>:<index>``): sent back unchanged.
    tool_call_id: re.Pattern[str] | None = None
    revision: str | None = None
    vllm_version: str = VLLM_VERSION
    trust_remote_code: bool = False
    image_input: bool = False
    source: str = ""
    read_on: str = ""

    @property
    def verified(self) -> bool:
        """The template's digest and the repository's revision are both recorded."""
        return self.template.sha256 is not None and self.revision is not None

    def serve_args(self, served_name: str | None = None) -> tuple[str, ...]:
        """``vllm serve`` as this row serves the model: the pinned revision, the parsers, auto tool choice."""
        args = ["vllm", "serve", self.repository]
        if self.revision is not None:
            args += ["--revision", self.revision]
        if served_name:
            args += ["--served-model-name", served_name]
        if self.template.source == "vllm":
            args += ["--chat-template", self.template.name]
        args += ["--enable-auto-tool-choice", "--tool-call-parser", self.tool_parser]
        if self.reasoning_parser is not None:
            args += ["--reasoning-parser", self.reasoning_parser]
        if self.trust_remote_code:
            args.append("--trust-remote-code")
        return tuple(args)


OPEN_MODEL_PINS: Final[tuple[OpenModelPin, ...]] = (
    OpenModelPin(
        key="qwen3-coder",
        repository="Qwen/Qwen3-Coder-30B-A3B-Instruct",
        served=re.compile(r"(?i)qwen3-coder"),
        template=ChatTemplate("chat_template.jinja"),
        tool_parser="qwen3_coder",
        source="https://docs.vllm.ai/en/v0.11.0/features/tool_calling.html",
        read_on="2026-11-06",
    ),
    OpenModelPin(
        key="kimi-k2",
        repository="moonshotai/Kimi-K2-Instruct-0905",
        served=re.compile(r"(?i)kimi-k2(?!-thinking)"),
        template=ChatTemplate("tokenizer_config.json#chat_template"),
        tool_parser="kimi_k2",
        tool_call_id=re.compile(r"functions\.[A-Za-z0-9_.-]+:\d+"),
        trust_remote_code=True,
        source="https://docs.vllm.ai/en/v0.11.0/features/tool_calling.html",
        read_on="2026-11-06",
    ),
    OpenModelPin(
        key="glm-4.6",
        repository="zai-org/GLM-4.6",
        served=re.compile(r"(?i)glm-4\.[56]"),
        template=ChatTemplate("chat_template.jinja"),
        tool_parser="glm45",
        reasoning_parser="glm45",
        source="https://docs.vllm.ai/en/v0.11.0/features/reasoning_outputs.html",
        read_on="2026-11-06",
    ),
)


def open_model_pin(model: str | None, pins: tuple[OpenModelPin, ...] = OPEN_MODEL_PINS) -> OpenModelPin | None:
    """The row serving ``model`` (an endpoint's model id, with or without its organisation); ``None`` for any other."""
    if not model:
        return None
    return next((pin for pin in pins if pin.served.search(model)), None)


def template_digest(text: str) -> str:
    """What a row records of a template: the SHA-256 of its text as vLLM renders with it."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_template(pin: OpenModelPin, text: str) -> TemplateStatus:
    """A running server's template against the row: ``mismatch`` when it is not the pinned one."""
    if pin.template.sha256 is None:
        return "unverified"
    return "verified" if template_digest(text) == pin.template.sha256 else "mismatch"


__all__ = [
    "OPEN_MODEL_PINS",
    "REASONING_FIELDS",
    "VLLM_IMAGE",
    "VLLM_VERSION",
    "ChatTemplate",
    "OpenModelPin",
    "ReasoningField",
    "TemplateStatus",
    "check_template",
    "open_model_pin",
    "template_digest",
]
