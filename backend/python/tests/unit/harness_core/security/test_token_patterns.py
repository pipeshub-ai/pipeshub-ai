"""FP-1..FP-3: vendor credentials found anywhere in tool arguments; look-alikes and scrub-only shapes are not."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core.security import (
    FORBIDDEN_PATTERN_SOURCES,
    VENDOR_TOKEN_PATTERNS,
    find_forbidden,
    find_forbidden_in,
)

# Built at run time so the source holds no literal credential shapes.
SAMPLES = {
    "anthropic_key": "sk-ant-" + "api03-" + "x" * 30,
    "openai_key": "sk-" + "proj-" + "Q" * 30,
    "slack_token": "xox" + "b-" + "1234567890-abcdef",
    "github_token": "gh" + "p_" + "a" * 36,
    "gitlab_token": "gl" + "pat-" + "b" * 20,
    "aws_access_key": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "google_api_key": "AI" + "za" + "c" * 35,
    "pipeshub_pat": "ph" + "pat_" + "d" * 20,
}
SCRUB_ONLY = {
    "jwt": "ey" + "JhbGciOi" + "." + "eyJzdWIi" + "x" * 4 + "." + "s" * 12,
    "private_key": "-----BEGIN " + "PRIVATE KEY-----\nMIIE\n-----END " + "PRIVATE KEY-----",
}


def test_every_kind_has_a_sample() -> None:
    assert set(SAMPLES) | set(SCRUB_ONLY) == {p.kind for p in VENDOR_TOKEN_PATTERNS}
    assert set(SAMPLES) == {p.kind for p in VENDOR_TOKEN_PATTERNS if p.forbidden}


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_fp1_each_vendor_credential_is_found_in_text(kind: str) -> None:
    assert kind in find_forbidden(f"please use {SAMPLES[kind]} for this")


@pytest.mark.parametrize("kind", sorted(SCRUB_ONLY))
def test_fp2_jwts_and_private_keys_are_redacted_not_refused(kind: str) -> None:
    assert find_forbidden(f"the document holds {SCRUB_ONLY[kind]}") == ()
    shape = next(p for p in VENDOR_TOKEN_PATTERNS if p.kind == kind)
    assert shape.pattern.search(SCRUB_ONLY[kind])


def test_fp3_the_proxy_gets_the_same_list_as_sources() -> None:
    import re

    assert FORBIDDEN_PATTERN_SOURCES
    for kind, sample in SAMPLES.items():
        assert any(re.search(source.encode(), sample.encode()) for source in FORBIDDEN_PATTERN_SOURCES), kind


@pytest.mark.parametrize(
    "where",
    ["value", "nested-list", "key", "deep"],
)
def test_fp1_found_anywhere_in_arguments(where: str) -> None:
    secret = SAMPLES["github_token"]
    arguments = {
        "value": {"body": secret},
        "nested-list": {"items": ["ok", ["also ok", secret]]},
        "key": {secret: "x"},
        "deep": {"a": {"b": {"c": [{"d": secret}]}}},
    }[where]
    assert find_forbidden_in(arguments) == ("github_token",)


@pytest.mark.parametrize(
    "text",
    [
        "sk-short",
        "task-ant-planning notes",
        "the risk-adjusted number",
        "eyJ alone is not a token",
        "AKIA lowercase akiaabcdefghijklmnop",
        "Q3 revenue grew 12% and churn fell",
        "aGVsbG8gd29ybGQgdGhpcyBpcyBiYXNlNjQ=",
    ],
)
def test_fp2_look_alikes_are_not_found(text: str) -> None:
    assert find_forbidden(text) == ()
    assert find_forbidden_in({"q": text, "n": 3, "flag": True, "none": None}) == ()


def test_lk7_the_shared_vector_file_is_this_list() -> None:
    """Node's leak tests read the list from ``contracts/harness/vectors/token-patterns.json`` (P2.4b)."""
    vectors = Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors" / "token-patterns.json"
    shared = json.loads(vectors.read_text())["patterns"]
    assert [(e["kind"], e["source"], e["forbidden"]) for e in shared] == [
        (p.kind, p.pattern.pattern, p.forbidden) for p in VENDOR_TOKEN_PATTERNS
    ]


@pytest.mark.parametrize("prefix", ["é", "ü", "日本", "é"])
def test_a_key_after_a_non_ascii_letter_is_still_found(prefix: str) -> None:
    # Found by the snapshot scan (P5.9b): ``\b`` read non-ASCII letters as word characters, hiding the key.
    assert "anthropic_key" in find_forbidden(f"{prefix}sk-ant-{'A' * 20}")
    assert "github_token" in find_forbidden(f"{prefix}ghp_{'a' * 24}")


@pytest.mark.parametrize("text", [f"task-{'a' * 24}", f"risk-{'b' * 24}", f"xAKIA{'A' * 16}"])
def test_a_shape_glued_to_an_ascii_word_is_not_a_key(text: str) -> None:
    assert find_forbidden(text) == ()
