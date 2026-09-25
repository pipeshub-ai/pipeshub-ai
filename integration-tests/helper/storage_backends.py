"""Which storage backends a run exercises, and whether it says so.

S3 is parked. These tests repoint the whole deployment at a backend through
``/storageConfig``, and that API has no endpoint field for S3 — see
``s3ConfigSchema`` in ``backend/nodejs/apps/src/modules/configuration_manager``.
Only a real AWS bucket can be reached, so the MinIO the integration stack
already runs cannot stand in, and every S3 case fails: 82 of them on the
2026-09-20 nightly, the first run where this suite executed at all.

Parking them is a deliberate, temporary choice to keep the nightly meaningful.
It is not a judgement that S3 works. Set ``PIPESHUB_STORAGE_S3=1`` to bring them
back once either of these is true:

  - the integration environment has AWS credentials and a bucket that work, or
  - ``/storageConfig`` accepts an endpoint, which would also let self-hosted
    installs point at MinIO or another S3-compatible store.
"""

from __future__ import annotations

import os

S3_OPT_IN = "PIPESHUB_STORAGE_S3"

_S3_CREDENTIAL_VARS = ("S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_REGION", "S3_BUCKET")


def s3_requested() -> bool:
    """Whether this run asked for the S3 cases."""
    return os.getenv(S3_OPT_IN, "").strip().lower() in {"1", "true", "yes"}


def available_backends() -> list[str]:
    """The backends to exercise: local always, S3 only when asked for.

    The credentials alone are not enough to turn S3 on. They are present in CI
    for the S3 *connector* tests, and before this opt-in existed that was all it
    took to switch these on too.
    """
    backends = ["local"]
    if not s3_requested():
        return backends
    if all(os.getenv(name) for name in _S3_CREDENTIAL_VARS):
        backends.append("s3")
    return backends


def missing_s3_credentials() -> list[str]:
    """The S3 variables with no value set."""
    return [name for name in _S3_CREDENTIAL_VARS if not os.getenv(name)]


def parked_notice() -> str | None:
    """A line for the run header, or None when S3 is actually in the run.

    Keyed off what the run ended up with, not what it asked for. Asking for S3
    and silently getting `["local"]` because a variable is unset is the same
    disappearance this whole module exists to prevent, so that case gets the
    loudest line of the three.
    """
    if "s3" in available_backends():
        return None
    if s3_requested():
        missing = ", ".join(missing_s3_credentials())
        return (
            f"storage: S3 was asked for but is NOT running - {missing} "
            f"{'is' if len(missing_s3_credentials()) == 1 else 'are'} unset. "
            "These cases are absent from the results below."
        )
    return (
        f"storage: S3 cases are parked (set {S3_OPT_IN}=1 to run them). "
        "/storageConfig has no endpoint field, so only a real AWS bucket can be "
        "reached and the stack's MinIO cannot stand in."
    )
