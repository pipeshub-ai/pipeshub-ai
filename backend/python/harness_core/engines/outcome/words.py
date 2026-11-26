"""The outcome's words by locale key (ADR-RUN-01 D1): the one catalog the presenter and the briefing read."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from harness_core.engines.outcome.locales import en

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_LOCALE: Final = "en"
LOCALES: Final[Mapping[str, Mapping[str, str]]] = {"en": en.MESSAGES}


class Words:
    """One locale's messages; a key it lacks falls back to the default locale's. An unknown key is a bug: it raises."""

    def __init__(self, locale: str = DEFAULT_LOCALE) -> None:
        self._messages = LOCALES.get(locale, LOCALES[DEFAULT_LOCALE])
        self._fallback = LOCALES[DEFAULT_LOCALE]

    def __call__(self, key: str, /, **params: object) -> str:
        template = self._messages.get(key) or self._fallback[key]
        return template.format(**params) if params else template

    def has(self, key: str) -> bool:
        return key in self._fallback


ENGLISH: Final = Words()

__all__ = ["DEFAULT_LOCALE", "ENGLISH", "LOCALES", "Words"]
