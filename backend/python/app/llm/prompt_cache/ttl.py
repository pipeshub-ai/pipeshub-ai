"""TTL ordering validation.

Anthropic requires any 1h-TTL breakpoints in a request to appear
BEFORE 5m-TTL breakpoints in prefix order — placing a 5m block ahead of
a 1h block is a 400. v1 ships 5m-only (no call site requests
`extended_ttl`; see the plan's settled decision against a 1h TTL in
v1), so `validate_ttl_ordering` can never raise today. It exists so
that whoever later wires up `extended_ttl` gets a failing unit test
instead of a runtime 400 discovered against a live account.
"""

from __future__ import annotations


class TTLOrderingError(ValueError):
    """Raised when a plan would place a 5m-TTL breakpoint before a
    1h-TTL breakpoint in the same request."""


def validate_ttl_ordering(ttls_in_order: list[str]) -> None:
    """`ttls_in_order` lists each breakpoint's TTL in the order it
    appears in the request payload (e.g. tools, then system, then
    message breakpoints in ascending message order). Raises the
    moment a "1h" is seen after a "5m" has already appeared.
    """
    seen_5m = False
    for ttl in ttls_in_order:
        if ttl == "5m":
            seen_5m = True
        elif ttl == "1h" and seen_5m:
            raise TTLOrderingError(
                "a 1h-TTL cache breakpoint appears after a 5m-TTL breakpoint; "
                "Anthropic requires 1h blocks to precede 5m blocks in the prefix"
            )


__all__ = ["TTLOrderingError", "validate_ttl_ordering"]
