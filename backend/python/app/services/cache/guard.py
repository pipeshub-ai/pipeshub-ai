"""Deterministic guard for Semantic Caching (plain-RAG internal_search).

Implements Section 4.4 (a-f) of the Semantic Caching specification:
1. Normalization (Unicode NFKC, quote straightening, whitespace collapse).
2. Rule (a) Numbers & Units: currencies, decimals, percentages, scale suffixes,
   spelled numbers (single and compound like twenty-five, two hundred), and ordinals.
3. Rule (b) Periods: 4-digit years, quarters (q1-q4, ordinal+quarter), halves (h1/h2),
   fiscal years (fy24, fy2024), months (with 'may' always treated as month token).
4. Rule (c) Negation & Polarity: negation words (not, no, never, without, neither, nor, except, cannot, can't),
   n't contractions, polarity antonym pairs, and prefix heuristics (un-, in-, im-, non-, dis-, il-, ir-)
   with an exemption list for common English words.
5. Rule (d) Direction & Quantifier Words: over, under, above, below, more, less, fewer, greater,
   at least, at most, before, after, since, until, between, highest, lowest, max, min,
   increase, decrease, top, bottom, earliest, latest, all, any, only, none, every.
6. Rule (e) Named Entities: capitalized tokens in original text (excluding sentence starters),
   digit+letter tokens (2FA, OAuth2, 401k), quoted strings, emails, URLs, technical IDs.
7. Rule (f) Lexical Overlap & Filler Allowlist: content tokens must be identical after stopword removal
   and light stemming, allowing differences ONLY from an explicit conversational/imperative filler allowlist.
   Secondary check: content-token Jaccard >= 0.60 and <= 2 absent tokens.
8. Language Support: is_guard_supported(q) checks for non-Latin script and non-English text.
9. Cache Key Normalization: normalize_for_key(q) for exact hits and deterministic IDs.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Common English stopwords for lexical content-token filtering
STOPWORDS: set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "as", "at", "be", "because", "been", "before", "being", "below",
    "between", "both", "but", "by", "could", "did", "do", "does", "doing", "down",
    "during", "each", "few", "for", "from", "further", "had", "has", "have",
    "having", "he", "her", "here", "hers", "herself", "him", "himself", "his",
    "how", "i", "if", "in", "into", "is", "it", "its", "itself", "let", "me",
    "more", "most", "my", "myself", "of", "off", "on", "once", "only", "or",
    "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same",
    "she", "should", "so", "some", "such", "than", "that", "the", "their",
    "theirs", "them", "themselves", "then", "there", "these", "they", "this",
    "those", "through", "to", "too", "under", "until", "up", "very", "was",
    "we", "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "with", "would", "you", "your", "yours", "yourself", "yourselves",
}

# Question / imperative starters excluded from capitalized named-entity extraction
SENTENCE_STARTERS: set[str] = {
    "what", "where", "when", "who", "whom", "which", "why", "how", "is", "are",
    "was", "were", "can", "could", "would", "should", "do", "does", "did", "tell",
    "show", "list", "give", "find", "summarize", "explain", "describe", "the", "a",
    "an", "please", "read", "check", "get", "see",
}

# Explicit conversational and search filler words allowed to differ in queries
FILLER_WORDS: set[str] = {
    "please", "kindly", "tell", "give", "show", "me", "can", "could", "would",
    "how", "do", "i", "explain", "describe", "list", "summarize", "find",
    "get", "check", "see", "help",
}

# Ordinals canonicalized to standard representation
ORDINAL_MAP: dict[str, str] = {
    "first": "1st", "1st": "1st",
    "second": "2nd", "2nd": "2nd",
    "third": "3rd", "3rd": "3rd",
    "fourth": "4th", "4th": "4th",
    "fifth": "5th", "5th": "5th",
    "sixth": "6th", "6th": "6th",
    "seventh": "7th", "7th": "7th",
    "eighth": "8th", "8th": "8th",
    "ninth": "9th", "9th": "9th",
    "tenth": "10th", "10th": "10th",
}

# Numerical components for English compound number parsing
SMALL_NUMBERS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}

TENS_NUMBERS: dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

SCALES: dict[str, int] = {
    "hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000,
}

ALL_NUM_WORDS: set[str] = set(SMALL_NUMBERS.keys()) | set(TENS_NUMBERS.keys()) | set(SCALES.keys())

# Months mapping (abbreviations and full names to canonical lowercase)
MONTH_MAP: dict[str, str] = {
    "jan": "january", "january": "january",
    "feb": "february", "february": "february",
    "mar": "march", "march": "march",
    "apr": "april", "april": "april",
    "may": "may",
    "jun": "june", "june": "june",
    "jul": "july", "july": "july",
    "aug": "august", "august": "august",
    "sep": "september", "sept": "september", "september": "september",
    "oct": "october", "october": "october",
    "nov": "november", "november": "november",
    "dec": "december", "december": "december",
}

# Curated polarity antonym pairs: (canonical_stem, positive_variants, negative_variants)
POLARITY_PAIRS: list[tuple[str, set[str], set[str]]] = [
    ("approved", {"approved"}, {"unapproved"}),
    ("active", {"active"}, {"inactive"}),
    ("enabled", {"enabled", "enable"}, {"disabled", "disable"}),
    ("allowed", {"allowed", "allow"}, {"disallowed", "disallow", "prohibited", "prohibit"}),
    ("compliant", {"compliant"}, {"non-compliant", "noncompliant"}),
    ("eligible", {"eligible"}, {"ineligible"}),
    ("valid", {"valid"}, {"invalid"}),
    ("paid", {"paid", "pay"}, {"unpaid"}),
    ("open", {"open"}, {"closed", "close"}),
    ("public", {"public"}, {"private"}),
    ("success", {"success", "successful"}, {"failure", "fail", "failed"}),
    ("pass", {"pass", "passed"}, {"fail", "failed"}),
    ("include", {"include", "includes", "including", "included"}, {"exclude", "excludes", "excluding", "excluded"}),
    ("legal", {"legal"}, {"illegal"}),
    ("authorized", {"authorized", "authorised"}, {"unauthorized", "unauthorised"}),
    ("direct", {"direct"}, {"indirect"}),
    ("complete", {"complete"}, {"incomplete"}),
    ("regular", {"regular"}, {"irregular"}),
    ("responsible", {"responsible"}, {"irresponsible"}),
    ("possible", {"possible"}, {"impossible"}),
]

POLARITY_LOOKUP: dict[str, tuple[str, str]] = {}
for _stem, _pos_set, _neg_set in POLARITY_PAIRS:
    for _w in _pos_set:
        POLARITY_LOOKUP[_w] = (_stem, "+")
    for _w in _neg_set:
        POLARITY_LOOKUP[_w] = (_stem, "-")

# Common words starting with negative prefixes that are NOT negated forms
PREFIX_EXEMPTIONS: set[str] = {
    "information", "interest", "income", "unit", "universal", "important",
    "import", "impact", "inside", "input", "issue", "index", "industry",
    "initial", "instance", "install", "instead", "instruction", "instructions",
    "instrument", "insurance", "intend", "internal", "internet", "into",
    "invite", "invoice", "under", "until", "union", "unique", "university",
    "unless", "update", "upgrade", "upon", "upper", "upset", "urban",
    "urge", "urgent", "use", "user", "usual", "utility", "discuss",
    "distance", "district", "display", "dispatch", "discover", "distinct",
    "distribute", "image", "immediate", "immune", "illustrate", "illuminate",
    "iron", "irrigate", "inline", "increase", "decrease",
}

# Curated direction and quantifier words (Section 4.4.d)
DIRECTION_QUANTIFIERS: set[str] = {
    "over", "under", "above", "below", "more", "less", "fewer", "greater",
    "at least", "at most", "before", "after", "since", "until", "between",
    "highest", "lowest", "max", "min", "increase", "decrease", "top",
    "bottom", "earliest", "latest", "all", "any", "only", "none", "every",
}

# Common English vocabulary words for language support validation
COMMON_ENGLISH_WORDS: set[str] = (
    STOPWORDS
    | SENTENCE_STARTERS
    | FILLER_WORDS
    | set(MONTH_MAP.keys())
    | DIRECTION_QUANTIFIERS
    | set(POLARITY_LOOKUP.keys())
    | ALL_NUM_WORDS
    | {
        "work", "policy", "refund", "handbook", "employee", "customer", "office",
        "report", "leave", "service", "system", "contract", "date", "status",
        "manager", "team", "travel", "expense", "request", "access", "support",
        "document", "file", "parental", "vacation", "holiday", "schedule",
        "benefits", "purchase", "order", "approve", "onboarding", "checklist",
        "account", "revenue", "budget", "quarter", "transfer", "deals", "growth",
        "price", "fee", "renewal", "hired", "branch", "latency", "sales",
        "meeting", "project", "task", "ticket", "data", "help", "server",
        "deploy", "deployment", "guide", "guidelines", "error", "troubleshoot",
        "channel", "allowance", "meal", "limit", "brief", "engineer", "engineering",
        "platform", "microservice", "microservices", "production", "regional",
        "regions", "positive", "department", "departmental", "milestones",
        "summary", "executive", "covered", "showed",
    }
)


def normalize_query(q: str) -> str:
    """Normalize query text: Unicode NFKC, straighten quotes, collapse whitespace."""
    if not q:
        return ""
    q = unicodedata.normalize("NFKC", q)
    # Curly quotes to straight
    q = q.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    # Collapse whitespace
    return re.sub(r"\s+", " ", q).strip()


def normalize_for_key(q: str) -> str:
    """Normalize query for cache keys and exact matching.

    Performs Unicode NFKC, lowercase, quote straightening, sentence-level punctuation
    removal (preserving numbers, decimals, currency, and %, which remain distinct),
    and whitespace collapsing.
    """
    if not q:
        return ""
    q = unicodedata.normalize("NFKC", q)
    q = q.lower()
    q = q.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    # Strip sentence-level punctuation except characters inside numbers/identifiers
    q = re.sub(r'[\?!\'\";:\(\)\[\]\{\}\<\>]', "", q)
    q = re.sub(r"(?<!\d)\.(?!\d)|(?<!\d),(?!\d)", " ", q)
    q = re.sub(r"\.(?=\s|$)", "", q)
    return re.sub(r"\s+", " ", q).strip()


def is_guard_supported(query: str) -> bool:
    """Check if query is supported by English deterministic guard.

    Returns False if the query contains non-Latin scripts, or if its tokens are
    mostly not English. Unsupported queries bypass the semantic cache in Phase 4.
    """
    if not query:
        return False
    norm = unicodedata.normalize("NFKC", query)
    # 1. Non-Latin script detection
    for ch in norm:
        if ch.isalpha():
            name = unicodedata.name(ch, "")
            if not name.startswith("LATIN"):
                return False
    # 2. Mostly English check
    words = [w for w in re.findall(r"[a-z]+", norm.lower()) if len(w) >= 2]
    if not words:
        return True
    english_count = sum(1 for w in words if w in COMMON_ENGLISH_WORDS or light_stem(w) in COMMON_ENGLISH_WORDS)
    return (english_count / len(words)) >= 0.50


def light_stem(word: str) -> str:
    """Light hand-written plural/inflection stemmer (zero external dependencies)."""
    w = word.lower()
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("es") and len(w) > 4 and any(w.endswith(s) for s in ("shes", "ches", "sses", "xes", "zes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


FILLER_STEMS: set[str] = {light_stem(w) for w in FILLER_WORDS}


def parse_compound_number_sequence(words: list[str]) -> int:
    """Evaluate consecutive English number words into their canonical integer value."""
    total = 0
    current = 0
    for w in words:
        if w in SMALL_NUMBERS:
            current += SMALL_NUMBERS[w]
        elif w in TENS_NUMBERS:
            current += TENS_NUMBERS[w]
        elif w == "hundred":
            current = (current if current != 0 else 1) * 100
        elif w in SCALES:
            scale = SCALES[w]
            total += (current if current != 0 else 1) * scale
            current = 0
    return total + current


def extract_guard_tokens(raw_text: str) -> dict[str, Any]:
    """Extract deterministic guard token sets across categories a-f."""
    norm = normalize_query(raw_text)
    lower = norm.lower()

    # --- a. Numbers & Units ---
    # Match currencies, decimals, percentages, scale suffixes ($50m, 5%, 5, EUR100, $100)
    raw_nums = re.findall(r"(?:[\$€£¥]|eur)?\b\d+(?:\.\d+)?(?:k|m|b|t|%)?\b%?", lower)
    clean_nums: set[str] = set()
    for m in raw_nums:
        m_str = m.strip()
        if m_str:
            clean_nums.add(m_str)

    tokens_raw = re.findall(r"\b[a-z0-9%'\-]+\b", lower)

    # Compound spelled-out number parsing ("twenty five" -> 25, "two hundred" -> 200)
    i = 0
    while i < len(tokens_raw):
        if tokens_raw[i] in ALL_NUM_WORDS:
            span = [tokens_raw[i]]
            j = i + 1
            while j < len(tokens_raw) and tokens_raw[j] in ALL_NUM_WORDS:
                span.append(tokens_raw[j])
                j += 1
            val = parse_compound_number_sequence(span)
            clean_nums.add(str(val))
            i = j
        else:
            if tokens_raw[i] in ORDINAL_MAP:
                clean_nums.add(ORDINAL_MAP[tokens_raw[i]])
            i += 1

    # --- b. Periods ---
    quarters: set[str] = set()
    for q in re.findall(r"\bq[1-4]\b", lower):
        quarters.add(q)
    if re.search(r"\b(first|1st)\s+quarter\b", lower):
        quarters.add("q1")
    if re.search(r"\b(second|2nd)\s+quarter\b", lower):
        quarters.add("q2")
    if re.search(r"\b(third|3rd)\s+quarter\b", lower):
        quarters.add("q3")
    if re.search(r"\b(fourth|4th)\s+quarter\b", lower):
        quarters.add("q4")

    halves: set[str] = set()
    for h in re.findall(r"\bh[1-2]\b", lower):
        halves.add(h)
    if re.search(r"\b(first|1st)\s+half\b", lower):
        halves.add("h1")
    if re.search(r"\b(second|2nd)\s+half\b", lower):
        halves.add("h2")

    # Fiscal years: fy24, fy2024, fiscal year 2024 -> canonical fy<year>
    fiscal_years: set[str] = set()
    for fy in re.findall(r"\bfy\s*(\d{2,4})\b", lower):
        fiscal_years.add(f"fy{fy}")
    for fy in re.findall(r"\bfiscal\s+year\s*(\d{2,4})\b", lower):
        fiscal_years.add(f"fy{fy}")

    # Calendar years: 4 digits (1900-2099)
    cal_years = set(re.findall(r"\b(19\d{2}|20\d{2})\b", lower))

    # Months: treat 'may' as month token always per section 4.4.b
    months: set[str] = set()
    for m_cand, m_canon in MONTH_MAP.items():
        if re.search(rf"\b{m_cand}\b", lower):
            months.add(m_canon)

    # --- c. Negation & Polarity ---
    negations: set[str] = set()
    for neg_word in ("not", "no", "never", "without", "neither", "nor", "except", "cannot", "can't"):
        if re.search(rf"\b{neg_word}\b", lower):
            negations.add(neg_word)
    # Contractions ending in n't
    for cont in re.findall(r"\b[a-z]+n't\b", lower):
        negations.add(f"contraction:{cont}")

    polarity_states: set[str] = set()
    for t in tokens_raw:
        clean_t = t.strip("-")
        if clean_t in POLARITY_LOOKUP:
            stem, sign = POLARITY_LOOKUP[clean_t]
            polarity_states.add(f"{stem}:{sign}")
        elif not clean_t.startswith(("non-", "non")):
            # Check prefixes outside exemption list
            for pfx in ("un", "in", "im", "dis", "il", "ir"):
                if (
                    clean_t.startswith(pfx)
                    and len(clean_t) > len(pfx) + 2
                    and clean_t not in PREFIX_EXEMPTIONS
                ):
                    base = clean_t[len(pfx):]
                    polarity_states.add(f"{base}:-")
                    break
        else:
            base = re.sub(r"^non-?", "", clean_t)
            polarity_states.add(f"{base}:-")

    # --- d. Direction & Quantifier words ---
    quantifiers: set[str] = set()
    if "at least" in lower:
        quantifiers.add("at least")
    if "at most" in lower:
        quantifiers.add("at most")
    for q_word in DIRECTION_QUANTIFIERS:
        if " " not in q_word and re.search(rf"\b{q_word}\b", lower):
            quantifiers.add(q_word)

    # --- e. Named Entities ---
    named_entities: set[str] = set()
    # 1. Emails
    for email in re.findall(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b", norm):
        named_entities.add(email.lower())
    # 2. URLs
    for url in re.findall(r"\bhttps?://\S+|www\.\S+\b", norm):
        named_entities.add(url.lower())
    # 3. Technical IDs (PROJ-123, UUIDs)
    for tech_id in re.findall(r"\b[A-Za-z]+-\d+\b", norm):
        named_entities.add(tech_id.lower())
    # 4. Quoted strings
    for quoted in re.findall(r'"([^"]+)"|\'([^\']+)\'', norm):
        q_val = quoted[0] or quoted[1]
        if q_val:
            named_entities.add(q_val.lower().strip())
    # 5. Tokens mixing digits and letters (e.g. 2FA, OAuth2, H1B, K8s, 401k)
    for mix in re.findall(r"\b(?=[a-zA-Z]*\d)(?=\d*[a-zA-Z])[a-zA-Z0-9]+\b", norm):
        mix_low = mix.lower()
        if mix_low not in ORDINAL_MAP and not re.match(r"^(?:q[1-4]|h[1-2]|fy\d+)$", mix_low):
            named_entities.add(mix_low)
    # 6. Capitalized tokens in original text
    tokens_orig = re.findall(r"\b[A-Za-z0-9_']+\b", norm)
    for idx, tok in enumerate(tokens_orig):
        clean_tok = re.sub(r"'s$", "", tok)
        if clean_tok and clean_tok[0].isupper():
            low_tok = clean_tok.lower()
            # Exclude sentence starters (including contraction forms like Isn't)
            is_starter = low_tok in SENTENCE_STARTERS or (
                low_tok.endswith("n't") and low_tok[:-3] in SENTENCE_STARTERS
            )
            if idx == 0 and is_starter:
                continue
            # Exclude periods, ordinals, and months from named entities
            if re.match(r"^(?:q[1-4]|h[1-2]|fy\d+)$", low_tok) or low_tok in ORDINAL_MAP:
                continue
            if low_tok not in STOPWORDS and low_tok not in MONTH_MAP and low_tok not in (
                "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"
            ):
                named_entities.add(low_tok)

    # --- f. Content Tokens (for Lexical Overlap & Steering Protection) ---
    content_tokens: set[str] = set()
    period_tokens = quarters | halves | fiscal_years | cal_years | months
    negation_tokens = {
        "not", "no", "never", "without", "neither", "nor", "except", "cannot", "can't", "cant",
    }
    polarity_words = set(POLARITY_LOOKUP.keys())
    excluded_from_content = (
        STOPWORDS
        | period_tokens
        | set(ORDINAL_MAP.keys())
        | set(ORDINAL_MAP.values())
        | named_entities
        | negation_tokens
        | polarity_words
        | DIRECTION_QUANTIFIERS
    )
    for t in tokens_raw:
        clean_t = re.sub(r"[^\w]", "", t)
        # Content tokens must contain letters and exclude tokens evaluated by rules a-e
        if (
            clean_t
            and any(c.isalpha() for c in clean_t)
            and clean_t not in excluded_from_content
            and not t.endswith("n't")
        ):
            stemmed = light_stem(clean_t)
            content_tokens.add(stemmed)

    return {
        "numbers": clean_nums,
        "quarters": quarters,
        "halves": halves,
        "fiscal_years": fiscal_years,
        "years": cal_years,
        "months": months,
        "negations": negations,
        "polarity": polarity_states,
        "quantifiers": quantifiers,
        "named_entities": named_entities,
        "content_tokens": content_tokens,
    }


def compare(query_a: str, query_b: str, jaccard_threshold: float = 0.60) -> tuple[bool, list[str]]:
    """Compare query_a and query_b under the hardened Section 4.4 guard rules (a-f).

    Args:
        query_a: First query string (e.g. incoming).
        query_b: Second query string (e.g. cached candidate).
        jaccard_threshold: Minimum content-token Jaccard similarity (default 0.60 per spec).

    Returns:
        (passed: bool, reasons: list[str]): True if all guard checks pass, False otherwise.
    """
    g_a = extract_guard_tokens(query_a)
    g_b = extract_guard_tokens(query_b)
    reasons: list[str] = []

    # a. Numbers & Units
    if g_a["numbers"] != g_b["numbers"]:
        reasons.append(f"numbers_mismatch: {g_a['numbers']} != {g_b['numbers']}")

    # b. Periods
    if g_a["quarters"] != g_b["quarters"]:
        reasons.append(f"quarter_mismatch: {g_a['quarters']} != {g_b['quarters']}")
    if g_a["halves"] != g_b["halves"]:
        reasons.append(f"half_year_mismatch: {g_a['halves']} != {g_b['halves']}")
    if g_a["fiscal_years"] != g_b["fiscal_years"]:
        reasons.append(f"fiscal_year_mismatch: {g_a['fiscal_years']} != {g_b['fiscal_years']}")
    if g_a["years"] != g_b["years"]:
        reasons.append(f"year_mismatch: {g_a['years']} != {g_b['years']}")
    if g_a["months"] != g_b["months"]:
        reasons.append(f"month_mismatch: {g_a['months']} != {g_b['months']}")

    # c. Negation & Polarity
    if g_a["negations"] != g_b["negations"]:
        reasons.append(f"negation_mismatch: {g_a['negations']} != {g_b['negations']}")
    if g_a["polarity"] != g_b["polarity"]:
        reasons.append(f"polarity_mismatch: {g_a['polarity']} != {g_b['polarity']}")

    # d. Direction / Quantifiers
    if g_a["quantifiers"] != g_b["quantifiers"]:
        reasons.append(f"quantifier_mismatch: {g_a['quantifiers']} != {g_b['quantifiers']}")

    # e. Named Entities
    if g_a["named_entities"] != g_b["named_entities"]:
        diff = g_a["named_entities"].symmetric_difference(g_b["named_entities"])
        reasons.append(f"named_entity_mismatch: {diff}")

    # f. Lexical Overlap & Strict Filler Allowlist
    set_a = g_a["content_tokens"]
    set_b = g_b["content_tokens"]
    diff_a = set_a - set_b
    diff_b = set_b - set_a

    # Any differing content token MUST belong to the explicit filler allowlist
    unallowed_a = {t for t in diff_a if t not in FILLER_STEMS}
    unallowed_b = {t for t in diff_b if t not in FILLER_STEMS}
    if unallowed_a or unallowed_b:
        reasons.append(
            f"content_tokens_mismatch: unallowed differences a_not_b={unallowed_a}, b_not_a={unallowed_b}"
        )

    # Secondary Jaccard and absent tokens check
    if set_a and set_b:
        intersection = len(set_a.intersection(set_b))
        union = len(set_a.union(set_b))
        jaccard = intersection / union if union > 0 else 1.0

        if jaccard < jaccard_threshold:
            reasons.append(f"jaccard_below_threshold: {jaccard:.2f} < {jaccard_threshold:.2f}")
        if len(diff_a) > 2 or len(diff_b) > 2:
            reasons.append(f"excess_absent_tokens: a_not_b={diff_a}, b_not_a={diff_b}")
    elif set_a != set_b:
        reasons.append(f"empty_content_tokens_mismatch: {set_a} != {set_b}")

    return len(reasons) == 0, reasons
