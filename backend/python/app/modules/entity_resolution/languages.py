"""Canonical language names.

Extraction is asked for ISO language names but returns codes, regional tags
and native spellings too. Languages are a closed set, so they are mapped
through this table instead of the vector and model tiers. An unknown value
falls through to plain normalization in the resolver.
"""

from __future__ import annotations

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


def canonical_language(raw: str) -> str | None:
    """The canonical English name for ``raw``, or ``None`` when unknown."""
    normalized = normalize_name(raw)
    if not normalized:
        return None
    if normalized in _LOOKUP:
        return _LOOKUP[normalized]
    # "en_US" / "en-us" style tags: try the primary subtag.
    primary = normalized.replace("_", "-").split("-", 1)[0]
    return _LOOKUP.get(primary)


__all__ = ["canonical_language"]
