"""SEC4-1..3: the exact values a call resolved, chosen from credential records and masked by the shared redactor."""

from __future__ import annotations

import asyncio

from harness_core.activity.redaction import StreamRedactor, shared_redactor
from harness_core.security.secrets import (
    EMPTY_SECRETS,
    SecretSet,
    active_secrets,
    masking,
)
from harness_core.security.token_patterns import SECRET_MASK, redact_tokens
from harness_core.telemetry.masking import masked_attributes
from harness_core.testing.canary import secret_canary
from harness_core.testing.ids import SeededIds
from tests.support.token_vector import EXAMPLES

IDS = SeededIds(4004)
CLIENT_SECRET = secret_canary(IDS.uuid4)
ACCESS = secret_canary(IDS.uuid4)
HEADER = "hv-" + IDS.uuid4().hex
MODEL_KEY = "mk-" + IDS.uuid4().hex
VENDOR = next(example for _, example, entry in EXAMPLES if entry.forbidden)

TOOLSET_RECORD = {
    "isAuthenticated": True,
    "auth": {"type": "OAUTH", "baseUrl": "https://acme.atlassian.net", "email": "dev@acme.example", "clientSecret": CLIENT_SECRET},
    "credentials": {
        "access_token": ACCESS,
        "token_type": "Bearer",
        "scope": "read:jira-work write:jira-work",
        "expires_at": "2026-10-10T12:00:00Z",
        "password": "short",
    },
    "apiKeys": [VENDOR, "tiny"],
}


def test_sec4_1_a_toolset_record_gives_its_secret_named_values_only() -> None:
    found = SecretSet.of(TOOLSET_RECORD)

    text = " ".join(
        [CLIENT_SECRET, ACCESS, VENDOR, "Bearer", "OAUTH", "https://acme.atlassian.net", "dev@acme.example",
         "read:jira-work write:jira-work", "2026-10-10T12:00:00Z", "short"]
    )
    assert found.mask(text).split(" ") == [
        SECRET_MASK, SECRET_MASK, SECRET_MASK, "Bearer", "OAUTH", "https://acme.atlassian.net", "dev@acme.example",
        "read:jira-work", "write:jira-work", "2026-10-10T12:00:00Z", "short",
    ]
    assert len(found) == 3


def test_sec4_1_an_mcp_header_value_is_a_secret_only_when_the_source_names_its_key() -> None:
    record = {"credentials": {"headerName": "X-Org-Key", "headerValue": HEADER}, "oauthTokens": {"accessToken": ACCESS}}

    assert SecretSet.of(record).mask(f"{HEADER} {ACCESS}") == f"{HEADER} {SECRET_MASK}"
    assert SecretSet.of(record, extra_keys=("headerValue",)).mask(f"{HEADER} {ACCESS}") == f"{SECRET_MASK} {SECRET_MASK}"


def test_sec4_1_a_bare_model_key_is_a_secret_and_the_set_never_prints_a_value() -> None:
    found = SecretSet.of(MODEL_KEY)

    assert found.mask(f"401: key {MODEL_KEY} refused") == f"401: key {SECRET_MASK} refused"
    for shown in (repr(found), str(found), f"{found!r}"):
        assert MODEL_KEY not in shown
        assert "1" in shown


def test_sec4_2_longer_values_are_masked_first_so_no_tail_is_left() -> None:
    inner = "abcdefgh12"
    outer = inner + "-suffix-xyz"
    found = SecretSet.of({"token": inner}) | SecretSet.of({"secret": outer})

    assert found.mask(f"{outer} and {inner}") == f"{SECRET_MASK} and {SECRET_MASK}"


def test_sec4_2_an_empty_set_changes_nothing() -> None:
    assert not EMPTY_SECRETS
    assert EMPTY_SECRETS.mask("any text at all") == "any text at all"
    assert EMPTY_SECRETS.mask_in({"k": ["v"]}) == {"k": ["v"]}


def test_sec4_2_mask_in_reaches_nested_strings_and_keys() -> None:
    found = SecretSet.of({"token": ACCESS})

    masked = found.mask_in({"ok": [f"x {ACCESS} y", {ACCESS: 1}], "n": 3})

    assert masked == {"ok": [f"x {SECRET_MASK} y", {SECRET_MASK: 1}], "n": 3}


def test_sec4_3_the_shared_redactor_masks_the_active_set_inside_the_scope_only() -> None:
    found = SecretSet.of(TOOLSET_RECORD)
    text = f"failed for {CLIENT_SECRET} with {VENDOR}"

    assert redact_tokens(text) == f"failed for {CLIENT_SECRET} with {SECRET_MASK}"
    with masking(found):
        assert active_secrets() is found
        assert redact_tokens(text) == f"failed for {SECRET_MASK} with {SECRET_MASK}"
    assert active_secrets() is EMPTY_SECRETS
    assert redact_tokens(text) == f"failed for {CLIENT_SECRET} with {SECRET_MASK}"


async def test_sec4_3_a_task_started_inside_the_scope_inherits_it() -> None:
    found = SecretSet.of({"client_secret": CLIENT_SECRET})

    async def child() -> str:
        await asyncio.sleep(0)
        return redact_tokens(CLIENT_SECRET)

    with masking(found):
        inside = await asyncio.create_task(child())
    outside = await asyncio.create_task(child())

    assert (inside, outside) == (SECRET_MASK, CLIENT_SECRET)


def test_sec4_3_the_stream_redactor_and_span_attributes_mask_exactly_inside_a_call() -> None:
    found = SecretSet.of({"client_secret": CLIENT_SECRET})
    with masking(found):
        stream = StreamRedactor(shared_redactor(), hold_chars=16, key_hold_chars=4096)
        shown = stream.feed(f"echo {CLIENT_SECRET[:10]}") + stream.feed(f"{CLIENT_SECRET[10:]}\n") + stream.flush()
        attributes = masked_attributes({"tool.result": f"got {CLIENT_SECRET}", "args": (CLIENT_SECRET,)})

    assert shown == f"echo {SECRET_MASK}\n"
    assert attributes == {"tool.result": f"got {SECRET_MASK}", "args": (SECRET_MASK,)}
