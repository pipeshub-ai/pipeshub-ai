"""SEC-CR-07 (G-37): a secret pasted into a message is found by the shared vector's pasted-input rules.

Read from the contract file, not the generated module, so a rule added without an example, or a negative the rules
start to match, fails here and in the Node and frontend twins of this test.
"""

from __future__ import annotations

import json

import pytest

from harness_core.contracts.vectors.token_patterns import SECRET_MASK
from harness_core.security.pasted_secrets import (
    find_pasted_secrets,
    redact_pasted_secrets,
)
from tests.support.token_vector import EXAMPLES, VECTOR_PATH

_RAW = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
_PASTED = _RAW["pastedInput"]
_PASTED_EXAMPLES = tuple(
    (f"{entry['kind']}-{i}", entry["kind"], "".join(parts))
    for entry in (*_PASTED["detectors"], _PASTED["assigned"], _PASTED["contextual"])
    for i, parts in enumerate(entry["examples"])
)


@pytest.mark.parametrize(("case_id", "kind", "text"), _PASTED_EXAMPLES, ids=[c[0] for c in _PASTED_EXAMPLES])
def test_sec_cr_07_every_pasted_input_example_is_found_and_masked(case_id: str, kind: str, text: str) -> None:
    found = find_pasted_secrets(text)
    assert [f.kind for f in found] == [kind], case_id
    redacted, _ = redact_pasted_secrets(text)
    assert SECRET_MASK in redacted
    secret = text[found[0].start : found[0].end]
    assert secret not in redacted


@pytest.mark.parametrize(("case_id", "example", "entry"), EXAMPLES, ids=[c[0] for c in EXAMPLES])
def test_sec_cr_07_every_token_vector_shape_counts_as_pasted(case_id: str, example: str, entry: object) -> None:
    redacted, found = redact_pasted_secrets(f"use this one: {example} thanks")
    assert found, case_id
    assert example not in redacted


@pytest.mark.parametrize("text", _PASTED["negatives"])
def test_sec_cr_07_ordinary_text_is_not_a_secret(text: str) -> None:
    assert find_pasted_secrets(text) == ()
    assert redact_pasted_secrets(text) == (text, ())


def test_sec_cr_07_two_secrets_in_one_message_are_both_masked_and_the_prose_kept() -> None:
    first = "".join(_PASTED["detectors"][0]["examples"][0])
    second = "".join(_PASTED["assigned"]["examples"][0])
    text = f"Deploy with {first}\nand log in with {second}\nthen tell me what broke."
    redacted, found = redact_pasted_secrets(text)
    assert len(found) == 2
    assert redacted.startswith("Deploy with ")
    assert redacted.endswith("then tell me what broke.")
    assert first not in redacted
    assert second.split(": ", 1)[1] not in redacted
    assert repr(found[0]).count(first) == 0
