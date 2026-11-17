"""The cache key: org first, record in the path, and every input that changes the answer changes the key."""

from __future__ import annotations

import subprocess
import sys

import pytest

from app.services.paid_cache.keys import (
    CacheKey,
    llm_params,
    model_identity,
    params_digest,
    prompt_digest,
    sha256_hex,
)


def _key(**changes: object) -> CacheKey:
    base: dict[str, object] = {
        "org_id": "org-1", "record_id": "rec-1", "kind": "ocr_page", "kind_version": 1,
        "model_identity": "ChatOpenAI:gpt-4o@api.openai.com", "prompt_digest": prompt_digest("describe the page"),
        "params_digest": params_digest({"temperature": 0, "max_tokens": 4096}), "input_digest": sha256_hex("page-1"),
    }
    base.update(changes)
    return CacheKey(**base)  # type: ignore[arg-type]


class _Llm:
    def __init__(self, **attrs: object) -> None:
        self.__dict__.update(attrs)


def test_key_layout_golden() -> None:
    # The digest is part of the storage layout: a change re-pays every cached result once, so it is pinned,
    # and it is the sha256 of the documented field list.
    pinned = CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", "i")
    assert pinned.digest() == "f173975b6485352c10dfab936b6543de7a4bbe563ccef1b8214fa1e697ea0214"
    assert pinned.digest() == sha256_hex("v1|org-1|rec-1|ocr_page|1|m|p|q|i")
    key = _key()
    assert key.path() == f"org-1/PipesHub/paid-results/rec-1/ocr_page/{key.digest()}.json"
    assert key.relative_path() == f"ocr_page/{key.digest()}.json"
    assert len(key.digest()) == 64


def test_the_digest_is_stable_across_processes() -> None:
    """Three interpreters with three different hash seeds (not isolated mode, which would ignore the seed)."""
    import os
    from pathlib import Path

    code = (
        "import sys;from app.services.paid_cache.keys import CacheKey;"
        "print(sys.flags.hash_randomization, CacheKey('org-1','rec-1','ocr_page',1,'m','p','q','i').digest())"
    )
    root = str(Path(__file__).resolve().parents[4])
    outputs = {}
    for seed in ("1", "2", "random"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": root}
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env, cwd=root,
        ).stdout.split()
        outputs[seed] = out[1]
    assert set(outputs.values()) == {CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", "i").digest()}


def test_the_org_purge_route_name_is_not_a_record_id() -> None:
    from app.services.paid_cache.keys import valid_record_id

    assert not valid_record_id("org")
    assert valid_record_id("Org") and valid_record_id("org-1") and valid_record_id("rec_1")


@pytest.mark.parametrize(
    "changes",
    [
        {"org_id": "org-2"},
        {"record_id": "rec-2"},
        {"kind": "image_description"},
        {"kind_version": 2},
        {"model_identity": "ChatOpenAI:gpt-4o-mini@api.openai.com"},
        {"prompt_digest": prompt_digest("describe the page, briefly")},
        {"params_digest": params_digest({"temperature": 0.7, "max_tokens": 4096})},
        {"params_digest": params_digest({"temperature": 0, "max_tokens": 8192})},
        {"input_digest": sha256_hex("page-2")},
    ],
)
def test_key_changes_with_model_prompt_and_params(changes: dict[str, object]) -> None:
    assert _key(**changes).digest() != _key().digest()
    assert _key(**changes).path() != _key().path()


def test_each_param_that_changes_the_answer_changes_the_params_digest() -> None:
    base = {"temperature": 0, "max_tokens": 4096}
    variants = [
        {**base, "temperature": 1},
        {**base, "max_tokens": 1024},
        {**base, "reasoning_effort": "high"},
        {**base, "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        {**base, "dpi": 300},
    ]
    digests = {params_digest(v) for v in [base, *variants]}
    assert len(digests) == len(variants) + 1


def test_params_that_are_unset_do_not_change_the_digest_and_order_does_not_matter() -> None:
    assert params_digest({"temperature": 0, "seed": None}) == params_digest({"temperature": 0})
    assert params_digest({"a": 1, "b": 2}) == params_digest({"b": 2, "a": 1})


def test_a_field_cannot_borrow_the_separator_to_forge_another_key() -> None:
    a = _key(model_identity="m|x", prompt_digest="p")
    b = _key(model_identity="m", prompt_digest="x|p")
    assert a.digest() != b.digest()


def test_the_model_identity_is_class_model_host_and_port_and_never_a_credential() -> None:
    llm = _Llm(model_name="gpt-4o", openai_api_base="https://user:secret@api.example.com:8443/v1?key=abc")
    identity = model_identity(llm)
    assert identity == "_Llm:gpt-4o@api.example.com:8443"
    assert "secret" not in identity and "abc" not in identity
    assert model_identity(_Llm(model="m")) == "_Llm:m"


def test_llm_params_read_what_the_model_carries_and_the_call_overrides_it() -> None:
    llm = _Llm(temperature=0.2, max_tokens=100, reasoning_effort="low")
    assert llm_params(llm) == {"temperature": 0.2, "max_tokens": 100, "reasoning_effort": "low"}
    assert llm_params(llm, temperature=0, extra_body={"x": 1})["temperature"] == 0


def test_an_unknown_kind_or_a_missing_record_cannot_make_a_key() -> None:
    with pytest.raises(ValueError, match="unknown paid-result kind"):
        _key(kind="made_up")
    with pytest.raises(ValueError, match="needs an org and a record"):
        _key(record_id="")
    with pytest.raises(ValueError, match="needs an org and a record"):
        _key(org_id="")


def test_the_endpoint_port_is_part_of_the_model_identity() -> None:
    from types import SimpleNamespace

    from app.services.paid_cache.keys import model_identity

    one = SimpleNamespace(model_name="m", base_url="http://localhost:8000/v1")
    two = SimpleNamespace(model_name="m", base_url="http://localhost:8001/v1")
    plain = SimpleNamespace(model_name="m", base_url="https://api.example.com/v1?key=secret")
    assert model_identity(one) != model_identity(two)
    assert model_identity(one).endswith("@localhost:8000")
    assert model_identity(plain).endswith("@api.example.com") and "secret" not in model_identity(plain)
