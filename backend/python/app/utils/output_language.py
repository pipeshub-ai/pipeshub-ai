"""The language a person reads answers in (G-52): their PipesHub locale, said to the model at the context tail.

The locale is a BCP 47 tag (``de-DE``), as Node takes it from the request's ``Accept-Language``. The section is a
turn section beside the time (``section_order``), so it never changes the prefix a provider caches (GA-27).
"""

from __future__ import annotations

import re
from typing import Final

# A BCP 47 language tag, as far as a prompt needs: a 2-3 letter language, then subtags. Anything else is ignored.
LOCALE_PATTERN: Final = r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$"
MAX_LOCALE_CHARS: Final = 35
_TAG = re.compile(LOCALE_PATTERN)

_HEADING: Final = "## Answer Language"
# The product's locales first (frontend/lib/i18n/locales), then common languages a browser may send.
_SCRIPTS: Final[dict[str, str]] = {"zh-hans": "Simplified Chinese", "zh-hant": "Traditional Chinese"}
_REGIONS: Final[dict[str, str]] = {
    "zh-cn": "Simplified Chinese", "zh-sg": "Simplified Chinese", "zh-tw": "Traditional Chinese",
    "zh-hk": "Traditional Chinese", "pt-br": "Brazilian Portuguese",
}
_LANGUAGES: Final[dict[str, str]] = {
    "en": "English", "de": "German", "es": "Spanish", "cs": "Czech", "hi": "Hindi", "ko": "Korean",
    "zh": "Chinese", "fr": "French", "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
    "ja": "Japanese", "ru": "Russian", "uk": "Ukrainian", "tr": "Turkish", "ar": "Arabic", "he": "Hebrew",
    "sv": "Swedish", "da": "Danish", "nb": "Norwegian", "no": "Norwegian", "fi": "Finnish", "el": "Greek",
    "hu": "Hungarian", "ro": "Romanian", "sk": "Slovak", "bg": "Bulgarian", "hr": "Croatian", "sr": "Serbian",
    "id": "Indonesian", "ms": "Malay", "th": "Thai", "vi": "Vietnamese", "bn": "Bengali", "ta": "Tamil",
    "te": "Telugu", "mr": "Marathi", "ur": "Urdu", "fa": "Persian",
}


def output_locale(value: str | None) -> str | None:
    """``value`` as a well-formed tag in its canonical case (``de-DE``, ``zh-Hant-TW``), or ``None``."""
    tag = (value or "").strip()
    if len(tag) > MAX_LOCALE_CHARS or not _TAG.match(tag):
        return None
    parts = tag.split("-")
    out = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            out.append(part.title())
        elif len(part) == 2 and part.isalpha():
            out.append(part.upper())
        else:
            out.append(part.lower())
    return "-".join(out)


def language_name(tag: str) -> str | None:
    """The English name of the language ``tag`` writes in, or ``None`` when this table does not know it."""
    lowered = tag.lower()
    parts = lowered.split("-")
    if len(parts) >= 2 and (name := _SCRIPTS.get(f"{parts[0]}-{parts[1]}")):
        return name
    if len(parts) >= 2 and (name := _REGIONS.get(f"{parts[0]}-{parts[-1]}")):
        return name
    return _LANGUAGES.get(parts[0])


def build_output_language(locale: str | None) -> str:
    """The turn section naming the person's answer language, or ``""`` without a well-formed locale."""
    tag = output_locale(locale)
    if tag is None:
        return ""
    name = language_name(tag)
    language = name if name is not None else f"the language tagged {tag}"
    shown = f"{name} ({tag})" if name is not None else language
    return (
        f"{_HEADING}\n\nThis person's PipesHub is set to {shown}. Write your answer in {language}, unless they write "
        "to you in another language or ask for a different one."
    )


__all__ = ["LOCALE_PATTERN", "MAX_LOCALE_CHARS", "build_output_language", "language_name", "output_locale"]
