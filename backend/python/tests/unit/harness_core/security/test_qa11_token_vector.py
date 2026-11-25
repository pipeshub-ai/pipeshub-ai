"""QA-11 QA11-5 and QA11-7 (XL-9): every entry of the shared token vector through every Python redactor and scanner.

The examples come from ``contracts/harness/vectors/token-patterns.json``; a redactor that keeps its own list, or a
pattern added there without an example, fails here.
"""

from __future__ import annotations

import re

import pytest

from harness_core.engines.snapshots import SnapshotArchive, SnapshotEntry, secret_kinds
from harness_core.security import (
    SECRET_MASK,
    find_forbidden,
    find_forbidden_in,
    redact_tokens,
)
from harness_core.testing.replay.scrub import Scrubber, find_secrets
from tests.support.token_vector import ENTRIES, EXAMPLES, MASK, VectorEntry, holding

EXAMPLE_IDS = [i for i, _, _ in EXAMPLES]
PARAMS = [(example, entry) for _, example, entry in EXAMPLES]


def surround(secret: str) -> str:
    return f'note: {secret}\n{{"auth": "{secret}"}}, end'


def test_qa11_7_every_entry_has_an_example_its_own_pattern_finds() -> None:
    kinds = [e.kind for e in ENTRIES]
    assert len(kinds) == len(set(kinds))
    for entry in ENTRIES:
        assert entry.examples, f"{entry.kind} has no example: every redactor test runs on them"
        for example in entry.examples:
            assert re.search(entry.source, example), entry.kind


def test_qa11_7_the_mask_a_person_sees_is_the_vector_mask() -> None:
    assert redact_tokens(EXAMPLES[0][1]) == SECRET_MASK == MASK


@pytest.mark.parametrize(("secret", "entry"), PARAMS, ids=EXAMPLE_IDS)
def test_qa11_5_redact_tokens_leaves_no_secret(secret: str, entry: VectorEntry) -> None:
    out = redact_tokens(surround(secret))
    assert secret not in out
    assert MASK in out
    assert out.startswith("note: ") and out.endswith(", end")


@pytest.mark.parametrize(("secret", "entry"), PARAMS, ids=EXAMPLE_IDS)
def test_qa11_5_the_replay_scrubber_leaves_no_secret_and_its_canary_scan_finds_none(
    secret: str, entry: VectorEntry
) -> None:
    scrubbed = Scrubber().value({"request": surround(secret), "nested": [surround(secret)]})
    assert not holding(secret, scrubbed)
    assert find_secrets(scrubbed) == []


@pytest.mark.parametrize(("secret", "entry"), PARAMS, ids=EXAMPLE_IDS)
def test_qa11_5_the_canary_scan_reports_each_entry_left_in(secret: str, entry: VectorEntry) -> None:
    found = find_secrets({"leaked": surround(secret)})
    assert any(line.startswith(f"{entry.kind}: ") for line in found), found


@pytest.mark.parametrize(
    ("secret", "entry"), [p for p in PARAMS if not p[1].redact_only], ids=[i for i, _, e in EXAMPLES if not e.redact_only]
)
def test_qa11_5_the_snapshot_scan_reports_every_vendor_kind(secret: str, entry: VectorEntry) -> None:
    archive = SnapshotArchive((SnapshotEntry("notes.txt", 0o644, 0, surround(secret).encode()),))
    assert entry.kind in secret_kinds(archive)


@pytest.mark.parametrize(("secret", "entry"), PARAMS, ids=EXAMPLE_IDS)
def test_qa11_5_the_gateways_refuse_exactly_the_forbidden_entries(secret: str, entry: VectorEntry) -> None:
    refused = entry.kind in find_forbidden(surround(secret))
    assert refused is entry.forbidden
    assert (entry.kind in find_forbidden_in({"args": [surround(secret)]})) is entry.forbidden
