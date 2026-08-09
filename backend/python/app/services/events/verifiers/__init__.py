"""Provider webhook verifiers.

Importing this package registers every verifier into the process-wide registry
(`base.get_verifier_registry()`); the ingress resolves `source_app` through
that registry, so a provider whose module is never imported is reported as an
unsupported app rather than silently accepted.
"""
from __future__ import annotations

from app.services.events.verifiers import github, jira, slack  # noqa: F401
from app.services.events.verifiers.base import (
    IAppEventVerifier,
    VerificationError,
    assert_within_replay_window,
    get_verifier_registry,
)

__all__ = [
    "IAppEventVerifier",
    "VerificationError",
    "assert_within_replay_window",
    "get_verifier_registry",
]
