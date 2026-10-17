"""Canonical language names.

Extraction is asked for ISO language names but returns codes, regional tags
and native spellings too. Languages are a closed set, so they are mapped
through this table instead of the vector and model tiers; a natural language
the table does not list is recognised through lingua (already the detector
used for sentence splitting). Anything else — Python, Go, JSON, YAML, which
extraction also reports as "languages" — is not a language and has no
canonical name.
"""

from __future__ import annotations

from functools import cache

from lingua import Language

from app.modules.entity_resolution.normalizer import normalize_name

# canonical English name -> ISO 639-1 code, common native and alternate names.
_LANGUAGES: dict[str, tuple[str, ...]] = {
    "English": ("en", "eng", "english (us)", "english (uk)", "en-us", "en-gb"),
    "French": ("fr", "fra", "fre", "français", "francais"),
    "German": ("de", "deu", "ger", "deutsch"),
    "Spanish": ("es", "spa", "español", "espanol", "castellano"),
    "Portuguese": ("pt", "por", "português", "portugues", "pt-br", "pt-pt"),
    "Italian": ("it", "ita", "italiano"),
    "Dutch": ("nl", "nld", "dut", "nederlands"),
    "Swedish": ("sv", "swe", "svenska"),
    "Norwegian": ("no", "nor", "nb", "nn", "norsk"),
    "Danish": ("da", "dan", "dansk"),
    "Finnish": ("fi", "fin", "suomi"),
    "Polish": ("pl", "pol", "polski"),
    "Czech": ("cs", "ces", "cze", "čeština", "cestina"),
    "Slovak": ("sk", "slk", "slo", "slovenčina"),
    "Hungarian": ("hu", "hun", "magyar"),
    "Romanian": ("ro", "ron", "rum", "română", "romana"),
    "Greek": ("el", "ell", "gre", "ελληνικά"),
    "Turkish": ("tr", "tur", "türkçe", "turkce"),
    "Russian": ("ru", "rus", "русский"),
    "Ukrainian": ("uk", "ukr", "українська"),
    "Hebrew": ("he", "heb", "iw", "עברית"),
    "Arabic": ("ar", "ara", "العربية"),
    "Persian": ("fa", "fas", "per", "farsi", "فارسی"),
    "Hindi": ("hi", "hin", "हिन्दी", "हिंदी"),
    "Bengali": ("bn", "ben", "bangla", "বাংলা"),
    "Tamil": ("ta", "tam", "தமிழ்"),
    "Telugu": ("te", "tel", "తెలుగు"),
    "Marathi": ("mr", "mar", "मराठी"),
    "Gujarati": ("gu", "guj", "ગુજરાતી"),
    "Kannada": ("kn", "kan", "ಕನ್ನಡ"),
    "Malayalam": ("ml", "mal", "മലയാളം"),
    "Punjabi": ("pa", "pan", "ਪੰਜਾਬੀ"),
    "Urdu": ("ur", "urd", "اردو"),
    "Chinese": (
        "zh", "zho", "chi", "zh-cn", "zh-tw", "zh-hans", "zh-hant",
        "mandarin", "simplified chinese", "traditional chinese", "中文",
    ),
    "Japanese": ("ja", "jpn", "日本語"),
    "Korean": ("ko", "kor", "한국어"),
    "Vietnamese": ("vi", "vie", "tiếng việt"),
    "Thai": ("th", "tha", "ไทย"),
    "Indonesian": ("id", "ind", "bahasa indonesia"),
    "Malay": ("ms", "msa", "may", "bahasa melayu"),
    "Filipino": ("tl", "fil", "tagalog"),
    "Swahili": ("sw", "swa", "kiswahili"),
}

_LOOKUP: dict[str, str] = {}
for _canonical, _aliases in _LANGUAGES.items():
    _LOOKUP[normalize_name(_canonical)] = _canonical
    for _alias in _aliases:
        _LOOKUP[normalize_name(_alias)] = _canonical


_LINGUA_DISPLAY = {Language.SLOVENE: "Slovenian"}


def _lingua_name(language: Language) -> str:
    # The table's name wins so "tl", "tgl" and "Tagalog" are one language.
    return (
        _LOOKUP.get(normalize_name(language.iso_code_639_1.name))
        or _LINGUA_DISPLAY.get(language)
        or language.name.replace("_", " ").title()
    )


@cache
def _lingua_lookup() -> dict[str, str]:
    # Two-letter codes are left out: outside the table they collide with
    # tokens that are not languages ("ts" is TypeScript before it is Tsonga).
    lookup: dict[str, str] = {}
    for language in Language.all():
        name = _lingua_name(language)
        for key in (name, language.name.replace("_", " "), language.iso_code_639_3.name):
            lookup[normalize_name(key)] = name
    return lookup


def canonical_language(raw: str) -> str | None:
    """The canonical English name for ``raw``, or ``None`` when it is not a
    natural language."""
    normalized = normalize_name(raw)
    if not normalized:
        return None
    # "en_US" / "en-us" style tags: try the primary subtag.
    primary = normalized.replace("_", "-").split("-", 1)[0]
    for key in (normalized, primary):
        if key in _LOOKUP:
            return _LOOKUP[key]
    lingua = _lingua_lookup()
    return lingua.get(normalized) or lingua.get(primary)


__all__ = ["canonical_language"]
