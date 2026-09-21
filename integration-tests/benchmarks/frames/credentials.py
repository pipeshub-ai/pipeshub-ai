"""Secrets come only from the environment and are never logged or persisted."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from benchmarks.frames.errors import MissingSecretError


@dataclass(frozen=True)
class Credentials:
    user_email: str | None = None
    user_password: str | None = field(default=None, repr=False)
    hf_token: str | None = field(default=None, repr=False)
    env: Mapping[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Credentials:
        source = dict(os.environ if env is None else env)
        return cls(
            user_email=source.get("PIPESHUB_TEST_USER_EMAIL") or None,
            user_password=source.get("PIPESHUB_TEST_USER_PASSWORD") or None,
            hf_token=source.get("HF_TOKEN") or None,
            env=source,
        )

    def require_user(self) -> tuple[str, str]:
        if not self.user_email or not self.user_password:
            raise MissingSecretError(
                "PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD must be set",
            )
        return self.user_email, self.user_password

    def first_of(self, names: Sequence[str]) -> str:
        """First non-empty value among `names` (e.g. a provider key and its fallback)."""
        for name in names:
            value = self.env.get(name)
            if value:
                return value
        raise MissingSecretError(f"none of {', '.join(names)} is set")
