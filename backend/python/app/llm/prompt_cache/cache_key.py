"""Builds the `prompt_cache_key` OpenAI uses as a cache-routing hint.

Must be scoped to BOTH org and user, not org alone: `_format_user_context`
renders name/email/org into the prompt's STABLE band (see the plan's
settled decision to keep it there rather than move it to the volatile
tail, which would risk "our/my org" resolution correctness for a
marginal cost gain). That means every user's rendered prefix is
already distinct at the content level; `prompt_cache_key` must route
each of those distinct prefixes to a distinct cache bucket too; an
org-only key would route unrelated per-user prefixes to the same
backend-side cache slot with no benefit — they would never actually
hit.

This is a ROUTING HINT, not an authorization boundary: a matched cache
key never substitutes for verifying org/user access to content, and
this function does not itself place any tenant's content into a
prefix shared with another tenant — every prefix component upstream of
this key is already scoped by the caller.
"""

from __future__ import annotations

import hashlib

_MAX_KEY_LENGTH = 128  # OpenAI's documented maximum for `prompt_cache_key`.


def _length_delimited(*fields: str) -> bytes:
    chunks: list[bytes] = []
    for field in fields:
        encoded = field.encode("utf-8")
        chunks.append(f"{len(encoded)}:".encode("ascii"))
        chunks.append(encoded)
    return b"".join(chunks)


def build_prompt_cache_key(*, org_id: str, user_id: str, spec_id: str = "") -> str:
    """`org_id` and `user_id` are required — a key scoped to org alone
    would let different users' prefixes contend for no benefit (see
    module docstring). `spec_id` is an optional caller-supplied
    disambiguator (e.g. an agent/model config id) for callers that
    want distinct cache buckets per agent configuration within the
    same org+user.
    """
    # Hash a length-delimited encoding of every field so a `:` inside a
    # value, or a field longer than 128 characters, cannot make two
    # distinct (org, user, spec) tuples share a routing key.
    digest = hashlib.sha256(
        _length_delimited(org_id, user_id, spec_id)
    ).hexdigest()
    return digest[:_MAX_KEY_LENGTH]


__all__ = ["build_prompt_cache_key"]
