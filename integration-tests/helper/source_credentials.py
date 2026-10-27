"""Whether a missing connector credential is a skip or a failure.

A connector suite whose credential is absent skips itself. On a developer's
machine that is right: nobody has every account. On the nightly run it hides
the thing we most want to know, because a rotated or expired secret looks
exactly like a passing run — the suite reports success having tested nothing.

So the run says which it is. ``PIPESHUB_REQUIRE_CONNECTOR_SECRETS=1`` (set by
the workflow for the nightly and for a run that deliberately picked one
connector) turns these into failures; everywhere else they stay skips.
"""

from __future__ import annotations

import os
from typing import NoReturn, Sequence

import pytest

REQUIRE_ENV = "PIPESHUB_REQUIRE_CONNECTOR_SECRETS"


def secrets_required() -> bool:
    """Is this a run that was supposed to exercise this connector?"""
    return os.getenv(REQUIRE_ENV, "").strip().lower() in {"1", "true", "yes"}


def missing_env(names: Sequence[str]) -> list[str]:
    """The names among ``names`` with no value set."""
    return [name for name in names if not os.getenv(name)]


def source_unavailable(
    reason: str, *, secrets: Sequence[str] = (), required: bool | None = None
) -> NoReturn:
    """Skip while developing, fail on a run that was meant to cover this.

    ``reason`` says what is missing in plain words. ``secrets`` names the
    environment variables to set, so whoever reads a red nightly knows what to
    do without opening the test.

    ``required`` overrides the decision for callers that are not connector
    suites, where shard membership does not say whether this run was meant to
    cover the thing. Left alone, the run's own flag decides.
    """
    if required is None:
        required = secrets_required()
    if not required:
        pytest.skip(reason)

    names = ", ".join(secrets)
    where = (
        f" Set {names} in the repository's integration-test environment"
        if names
        else " Set the credentials for this connector in the repository's"
        " integration-test environment"
    )
    pytest.fail(
        f"{reason}{where}, or remove this connector from the run's shard so the"
        " run does not claim to cover it."
    )
