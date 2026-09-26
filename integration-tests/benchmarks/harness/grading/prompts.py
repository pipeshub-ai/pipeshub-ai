"""Grader prompt templates, pinned by sha256.

The FRAMES auto-rater is the paper's Figure 6 prompt copied verbatim; the
strict grader is simple-evals' SimpleQA GRADER_TEMPLATE (MIT, © 2024 OpenAI)
copied verbatim. Editing a prompt file without bumping its version fails
`verify_prompt_pins()` and the unit test that calls it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from benchmarks.harness.errors import ConfigError
from benchmarks.harness.paths import PROMPTS_DIR

# Paper Figure 6 system message.
FRAMES_JUDGE_SYSTEM_PROMPT = "You are a helpful assistant."


@dataclass(frozen=True)
class PromptTemplate:
    version: str
    filename: str
    sha256: str

    @property
    def path(self) -> Path:
        return PROMPTS_DIR / self.filename

    def text(self) -> str:
        return _read(self.path)


@cache
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").rstrip("\n")


FRAMES_AUTORATER = PromptTemplate(
    "frames-autorater-v1", "frames_autorater_v1.txt",
    "003cf1dc92b2e18486b67629fc5a7944fdaba1d1be430236b08054387484bd5e",
)
SIMPLEQA_GRADER = PromptTemplate(
    "simpleqa-grader-v1", "simpleqa_grader_v1.txt",
    "a456b5c4c3af5bddf815edb1d54630f6f53c6d16d752ac48d9307bd4f186a3d6",
)
CLAIM_SUPPORT = PromptTemplate(
    "claim-support-v1", "claim_support_v1.txt",
    "6554a2f58167bcaf327899c2c4c4ec3168f65ed9be31f28409a2d9d8baf786de",
)
ALL_PROMPTS = (FRAMES_AUTORATER, SIMPLEQA_GRADER, CLAIM_SUPPORT)

# The prompts the systems under test ANSWER with. Pinned for the same reason
# the grader prompts are: editing one silently changes every number in the
# board, and a version string alone does not notice. Bump the version in the
# owning module and the sha here together.
ANSWER_PROMPT_PINS: dict[str, str] = {
    "frames-answer-v3": "ebb2d970d9d12f71ffc9f6f9510e8460a4bfa68f61cb394d17f8c0c4598f24cb",
    "frames-answer-grounded-v1": "d2eaae9a7dbbf583b4e546bbf2bff02ebe087c473fc433a29c3688b0accab01e",
    "rag-answer-v2": "b71b091d1c49d601bcf4397134c905c532ce7879ede7a42f9a4234c4c8962844",
    "rag-expand-v1": "e76faa0ef91e5b6b111ccfc904a628c188a165116346a567f1e8e6d7b5b2e1c6",
    "rag-decompose-v1": "8f47dd340531aa7fc64902d12a456021e47fff2c9ed936e386cf0907d5331f53",
}

_FRAMES_MARKERS = re.compile(r"<<(question|LLM_response|ground_truth_answer)>>")


def answer_prompt_texts() -> dict[str, str]:
    """version -> prompt text. Imported lazily: the systems import grading
    helpers, so a module-level import here would be a cycle."""
    from benchmarks.harness.systems.baselines.answering import (
        ANSWER_PROMPT_VERSION,
        GROUNDED_ANSWER_PROMPT_VERSION,
        _GROUNDED_SYSTEM_PROMPT as BASELINE_GROUNDED,
        _SYSTEM_PROMPT as BASELINE_SYSTEM,
    )
    from benchmarks.harness.systems.rag.answerer import (
        RAG_ANSWER_PROMPT_VERSION,
        _SYSTEM_PROMPT as RAG_SYSTEM,
    )
    from benchmarks.harness.systems.rag.transforms import (
        DECOMPOSITION_PROMPT_VERSION,
        EXPANSION_PROMPT_VERSION,
        _DECOMPOSITION_SYSTEM,
        _EXPANSION_SYSTEM,
    )

    return {
        ANSWER_PROMPT_VERSION: BASELINE_SYSTEM,
        GROUNDED_ANSWER_PROMPT_VERSION: BASELINE_GROUNDED,
        RAG_ANSWER_PROMPT_VERSION: RAG_SYSTEM,
        EXPANSION_PROMPT_VERSION: _EXPANSION_SYSTEM,
        DECOMPOSITION_PROMPT_VERSION: _DECOMPOSITION_SYSTEM,
    }


def verify_prompt_pins() -> None:
    for prompt in ALL_PROMPTS:
        actual = hashlib.sha256(prompt.path.read_bytes()).hexdigest()
        if actual != prompt.sha256:
            raise ConfigError(f"{prompt.filename} changed (sha256 {actual}); bump its version")
    for version, text in answer_prompt_texts().items():
        expected = ANSWER_PROMPT_PINS.get(version)
        actual = hashlib.sha256(text.encode()).hexdigest()
        if expected is None:
            raise ConfigError(f"answering prompt {version!r} is not pinned in ANSWER_PROMPT_PINS")
        if actual != expected:
            raise ConfigError(
                f"answering prompt {version!r} changed (sha256 {actual}); "
                "bump its version and update ANSWER_PROMPT_PINS",
            )


def render_frames_autorater(question: str, predicted: str, gold: str) -> str:
    """Single-pass substitution, so an answer that happens to contain a marker
    cannot inject text into another slot."""
    values = {"question": question, "LLM_response": predicted, "ground_truth_answer": gold}
    return _FRAMES_MARKERS.sub(lambda match: values[match.group(1)], FRAMES_AUTORATER.text())


def render_simpleqa_grader(question: str, target: str, predicted: str) -> str:
    return SIMPLEQA_GRADER.text().format(question=question, target=target, predicted_answer=predicted)


def render_claim_support(evidence: str, statement: str) -> str:
    return CLAIM_SUPPORT.text().format(evidence=evidence, statement=statement)
