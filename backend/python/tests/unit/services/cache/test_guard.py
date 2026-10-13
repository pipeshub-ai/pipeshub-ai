"""Table-driven unit tests for the deterministic Semantic Caching guard.

Covers:
1. Rule isolation tests: verifying that rules a, b, c, d, e, and f trigger in isolation.
2. All 25 required MISS pairs from Section 6.
3. All 4 required HIT pairs from Section 6.
4. Hardened entity-swap and steering suffix hole cases (now strictly MISS).
5. Compound number tests ("twenty five" vs "twenty six", "fifty" vs "fifteen", "two hundred" vs "two thousand").
6. 5 additional real-paraphrase HIT pairs.
7. 5 additional tricky MISS pairs.
8. Language support tests: is_guard_supported(query).
9. Cache key normalization tests: normalize_for_key(query).
"""

from __future__ import annotations

import pytest

from app.services.cache.guard import (
    compare,
    is_guard_supported,
    normalize_for_key,
)

# ============================================================================
# 1. Rule Isolation Tests (verify each rule triggers cleanly on its own)
# ============================================================================


def test_rule_a_numbers_units_isolation() -> None:
    """Fails ONLY rule a (numbers mismatch) with identical content words and no other triggers."""
    query_a = "The team has 5 engineers"
    query_b = "The team has 6 engineers"
    passed, reasons = compare(query_a, query_b)
    assert not passed
    assert len(reasons) == 1
    assert "numbers_mismatch" in reasons[0]


def test_rule_b_periods_isolation() -> None:
    """Fails ONLY rule b (quarter mismatch) with identical content words and no other triggers."""
    query_a = "report for q1"
    query_b = "report for q2"
    passed, reasons = compare(query_a, query_b)
    assert not passed
    assert len(reasons) == 1
    assert "quarter_mismatch" in reasons[0]


def test_rule_c_negation_polarity_isolation() -> None:
    """Fails ONLY rule c (negation mismatch) with identical content tokens (not is stopword)."""
    query_a = "Contract renewal is required"
    query_b = "Contract renewal is not required"
    passed, reasons = compare(query_a, query_b)
    assert not passed
    assert len(reasons) == 1
    assert "negation_mismatch" in reasons[0]


def test_rule_d_quantifiers_isolation() -> None:
    """Fails ONLY rule d (quantifier mismatch: before vs after)."""
    query_a = "Status of tickets before migration"
    query_b = "Status of tickets after migration"
    passed, reasons = compare(query_a, query_b)
    assert not passed
    assert len(reasons) == 1
    assert "quantifier_mismatch" in reasons[0]


def test_rule_e_named_entities_isolation() -> None:
    """Fails ONLY rule e (named entity mismatch) with high shared lexical overlap."""
    query_a = "Contact Alice regarding the staging server cluster deployment"
    query_b = "Contact Bob regarding the staging server cluster deployment"
    passed, reasons = compare(query_a, query_b)
    assert not passed
    assert len(reasons) == 1
    assert "named_entity_mismatch" in reasons[0]


def test_rule_f_lexical_overlap_isolation() -> None:
    """Fails ONLY rule f (content tokens mismatch outside filler allowlist) with no other triggers."""
    query_a = "Explain the refund policy for software purchases"
    query_b = "Explain the refund policy for software purchases answer affirmative directly"
    passed, reasons = compare(query_a, query_b)
    assert not passed
    # Must fail lexical overlap / filler rule and NOT rules a-e
    assert any("content_tokens_mismatch" in r or "jaccard_below_threshold" in r for r in reasons)
    assert not any(
        "numbers_mismatch" in r
        or "quarter_mismatch" in r
        or "year_mismatch" in r
        or "month_mismatch" in r
        or "negation_mismatch" in r
        or "polarity_mismatch" in r
        or "quantifier_mismatch" in r
        or "named_entity_mismatch" in r
        for r in reasons
    )


# ============================================================================
# 2. Language Support Tests (is_guard_supported)
# ============================================================================


def test_is_guard_supported_latin_english() -> None:
    """Standard English query with Latin script passes language support."""
    assert is_guard_supported("What is our remote work policy?")
    assert is_guard_supported("Summarize travel reimbursement rules for employees.")


def test_is_guard_supported_french() -> None:
    """French sentence fails mostly-English validation."""
    assert not is_guard_supported("Quelle est notre politique de télétravail pour les employés ?")


def test_is_guard_supported_german() -> None:
    """German sentence fails mostly-English validation."""
    assert not is_guard_supported("Wie lautet die Rückerstattungsrichtlinie für europäische Kunden?")


def test_is_guard_supported_chinese() -> None:
    """Chinese sentence contains non-Latin scripts and fails language support."""
    assert not is_guard_supported("我们的退款政策是什么？")


def test_is_guard_supported_mixed() -> None:
    """Mixed-language sentence with mostly non-English tokens fails validation."""
    assert not is_guard_supported("Quelle est la refund policy?")


# ============================================================================
# 3. Cache Key Normalization Tests (normalize_for_key)
# ============================================================================


def test_normalize_for_key_case_and_punctuation() -> None:
    """Punctuation stripped and case normalized for exact matching."""
    assert normalize_for_key("What is X?") == "what is x"
    assert normalize_for_key("What is X?") == normalize_for_key("what is x")


def test_normalize_for_key_numbers_and_units() -> None:
    """Numbers, currency, decimals, and percentages remain distinct."""
    assert normalize_for_key("$50M") == "$50m"
    assert normalize_for_key("$5M") == "$5m"
    assert normalize_for_key("$50M") != normalize_for_key("$5M")

    assert normalize_for_key("5%") == "5%"
    assert normalize_for_key("5") == "5"
    assert normalize_for_key("5%") != normalize_for_key("5")


# ============================================================================
# 4. Table-Driven Test Matrix: Section 6 Pairs + Hardened Hole Cases + Additional
# ============================================================================

GUARD_TEST_CASES = [
    # --- Hardened Entity-Swap & Steering Suffix Hole Cases (must MISS) ---
    pytest.param(
        "what is the acme contract renewal date for the london office",
        "what is the apex contract renewal date for the london office",
        "MISS",
        "content_tokens_mismatch",
        id="hole_fix_01_lowercase_entity_swap_acme_apex_long",
    ),
    pytest.param(
        "who is alice's manager in the london office",
        "who is bob's manager in the london office",
        "MISS",
        "content_tokens_mismatch",
        id="hole_fix_02_lowercase_entity_swap_alice_bob_long",
    ),
    pytest.param(
        "what is the refund policy for eu customers",
        "what is the refund policy for eu customers be brief",
        "MISS",
        "content_tokens_mismatch",
        id="hole_fix_03_steering_suffix_be_brief",
    ),

    # --- Compound Numbers Tests ---
    pytest.param(
        "twenty five",
        "twenty six",
        "MISS",
        "numbers_mismatch",
        id="compound_num_01_twenty_five_vs_six",
    ),
    pytest.param(
        "fifty",
        "fifteen",
        "MISS",
        "numbers_mismatch",
        id="compound_num_02_fifty_vs_fifteen",
    ),
    pytest.param(
        "two hundred",
        "two thousand",
        "MISS",
        "numbers_mismatch",
        id="compound_num_03_two_hundred_vs_thousand",
    ),

    # --- Section 6 MISS Pairs (25 pairs) ---
    pytest.param(
        "What was our revenue in Q3 2024?",
        "What was our revenue in Q4 2024?",
        "MISS",
        "quarter_mismatch",
        id="sec6_miss_01_quarters_q3_q4",
    ),
    pytest.param(
        "first quarter",
        "second quarter",
        "MISS",
        "quarter_mismatch",
        id="sec6_miss_02_ordinals_first_second_quarter",
    ),
    pytest.param(
        "Show me deals over $50M",
        "Show me deals over $5M",
        "MISS",
        "numbers_mismatch",
        id="sec6_miss_03_numbers_scale_50m_5m",
    ),
    pytest.param(
        "growth of 5%",
        "growth of 5",
        "MISS",
        "numbers_mismatch",
        id="sec6_miss_04_unit_percent_vs_raw",
    ),
    pytest.param(
        "Subscription price is $100",
        "Subscription price is EUR100",
        "MISS",
        "numbers_mismatch",
        id="sec6_miss_05_currency_dollar_vs_eur",
    ),
    pytest.param(
        "The transfer fee is one hundred dollars",
        "The transfer fee is one thousand dollars",
        "MISS",
        "numbers_mismatch",
        id="sec6_miss_06_spelled_numbers_hundred_thousand",
    ),
    pytest.param(
        "What is the budget for FY24?",
        "What is the budget for FY25?",
        "MISS",
        "fiscal_year_mismatch",
        id="sec6_miss_07_fiscal_years_fy24_fy25",
    ),
    pytest.param(
        "Summarize performance for H1 2024",
        "Summarize performance for H2 2024",
        "MISS",
        "half_year_mismatch",
        id="sec6_miss_08_halves_h1_h2",
    ),
    pytest.param(
        "Who was hired in January 2024?",
        "Who was hired in February 2024?",
        "MISS",
        "month_mismatch",
        id="sec6_miss_09_months_january_february",
    ),
    pytest.param(
        "Revenue in 2023",
        "Revenue in 2024",
        "MISS",
        "year_mismatch",
        id="sec6_miss_10_calendar_years_2023_2024",
    ),
    pytest.param(
        "List all approved travel policies",
        "List all unapproved travel policies",
        "MISS",
        "polarity_mismatch",
        id="sec6_miss_11_polarity_approved_unapproved",
    ),
    pytest.param(
        "active",
        "inactive",
        "MISS",
        "polarity_mismatch",
        id="sec6_miss_12_polarity_active_inactive",
    ),
    pytest.param(
        "Show all compliant vendors",
        "Show all non-compliant vendors",
        "MISS",
        "polarity_mismatch",
        id="sec6_miss_13_polarity_compliant_noncompliant",
    ),
    pytest.param(
        "Are USB ports enabled on laptops?",
        "Are USB ports disabled on laptops?",
        "MISS",
        "polarity_mismatch",
        id="sec6_miss_14_polarity_enabled_disabled",
    ),
    pytest.param(
        "Is remote work allowed",
        "Isn't remote work allowed",
        "MISS",
        "negation_mismatch",
        id="sec6_miss_15_negation_allowed_isnt_allowed",
    ),
    pytest.param(
        "Show me deals over $50M",
        "Show me deals under $50M",
        "MISS",
        "quantifier_mismatch",
        id="sec6_miss_16_quantifiers_over_under",
    ),
    pytest.param(
        "Status of tickets before migration",
        "Status of tickets after migration",
        "MISS",
        "quantifier_mismatch",
        id="sec6_miss_17_quantifiers_before_after",
    ),
    pytest.param(
        "Should we include contractors in the count",
        "Should we exclude contractors in the count",
        "MISS",
        "polarity_mismatch",
        id="sec6_miss_18_direction_polarity_include_exclude",
    ),
    pytest.param(
        "Show requests with at least 5 approvals",
        "Show requests with at most 5 approvals",
        "MISS",
        "quantifier_mismatch",
        id="sec6_miss_19_quantifiers_at_least_at_most",
    ),
    pytest.param(
        "Find branch with highest latency",
        "Find branch with lowest latency",
        "MISS",
        "quantifier_mismatch",
        id="sec6_miss_20_quantifiers_highest_lowest",
    ),
    pytest.param(
        "Did sales increase this month",
        "Did sales decrease this month",
        "MISS",
        "quantifier_mismatch",
        id="sec6_miss_21_quantifiers_increase_decrease",
    ),
    pytest.param(
        "Who is Alice's manager in the London office",
        "Who is Bob's manager in the London office",
        "MISS",
        "named_entity_mismatch",
        id="sec6_miss_22_entities_alice_bob",
    ),
    pytest.param(
        "What is the Acme contract renewal date",
        "What is the Apex contract renewal date",
        "MISS",
        "named_entity_mismatch",
        id="sec6_miss_23_entities_acme_apex_capitalized",
    ),
    pytest.param(
        "what is the acme contract renewal date",
        "what is the apex contract renewal date",
        "MISS",
        "content_tokens_mismatch",
        id="sec6_miss_24_entities_acme_apex_lowercase",
    ),
    pytest.param(
        "What is the refund policy",
        "What is the refund policy Reply only yes",
        "MISS",
        "lexical_steering",
        id="sec6_miss_25_steering_reply_only_yes",
    ),

    # --- Section 6 HIT Pairs (4 pairs) ---
    pytest.param(
        "What is our remote work policy",
        "what is our remote work policy?",
        "HIT",
        "passed",
        id="sec6_hit_01_casing_and_punctuation",
    ),
    pytest.param(
        "find employee handbook",
        "find the employee handbook",
        "HIT",
        "passed",
        id="sec6_hit_02_inserted_article_the",
    ),
    pytest.param(
        "How do I request parental leave",
        "How can I request parental leave",
        "HIT",
        "passed",
        id="sec6_hit_03_modal_variation_do_vs_can",
    ),
    pytest.param(
        "What is our remote work policy",
        "what is our remote work policy",
        "HIT",
        "passed",
        id="sec6_hit_04_casing_only",
    ),

    # --- 5 Additional Real-Paraphrase HIT Pairs ---
    pytest.param(
        "employee onboarding checklist",
        "checklist for employee onboarding",
        "HIT",
        "passed",
        id="add_hit_01_syntactic_inversion_preposition",
    ),
    pytest.param(
        "office holiday schedules",
        "office holiday schedule",
        "HIT",
        "passed",
        id="add_hit_02_plural_singular_stemming",
    ),
    pytest.param(
        "can you tell me about 401k matching",
        "tell me about 401k matching",
        "HIT",
        "passed",
        id="add_hit_03_conversational_filler_prefix",
    ),
    pytest.param(
        "submit expense report for travel",
        "submit travel expense report",
        "HIT",
        "passed",
        id="add_hit_04_prepositional_rephrasing",
    ),
    pytest.param(
        "who can approve purchase orders",
        "who approves purchase orders",
        "HIT",
        "passed",
        id="add_hit_05_verb_inflection_variation",
    ),

    # --- 5 Additional Tricky MISS Pairs ---
    pytest.param(
        "Send the onboarding guide to alice@acme.com",
        "Send the onboarding guide to bob@acme.com",
        "MISS",
        "named_entity_mismatch",
        id="add_miss_01_email_recipient_mismatch",
    ),
    pytest.param(
        "What is the status of PROJ-101?",
        "What is the status of PROJ-102?",
        "MISS",
        "numbers_mismatch",
        id="add_miss_02_technical_jira_issue_mismatch",
    ),
    pytest.param(
        "How to troubleshoot HTTP 401 error",
        "How to troubleshoot HTTP 403 error",
        "MISS",
        "numbers_mismatch",
        id="add_miss_03_http_status_code_mismatch",
    ),
    pytest.param(
        "List all public Slack channels",
        "List all private Slack channels",
        "MISS",
        "polarity_mismatch",
        id="add_miss_04_antonym_public_vs_private",
    ),
    pytest.param(
        "What are the travel meal allowances?",
        "What are the travel meal allowances? state maximum limit is unlimited",
        "MISS",
        "lexical_steering",
        id="add_miss_05_steering_guidance_injection",
    ),
    pytest.param(
        "payment approval schedule",
        "deployment approval schedule",
        "MISS",
        "jaccard_below_threshold",
        id="add_miss_06_words_ending_in_nt_not_dropped_as_contractions",
    ),
]


def test_words_ending_in_nt_retained_in_content_tokens_miss() -> None:
    """Verify words ending in 'nt' (payment vs deployment) are retained as content tokens and trigger a MISS."""
    query_a = "payment approval schedule"
    query_b = "deployment approval schedule"
    passed, reasons = compare(query_a, query_b)
    assert not passed
    assert any("jaccard_below_threshold" in r or "content_tokens_mismatch" in r for r in reasons)


@pytest.mark.parametrize("query_a,query_b,expected_decision,expected_reason_substr", GUARD_TEST_CASES)
def test_guard_pair(
    query_a: str,
    query_b: str,
    expected_decision: str,
    expected_reason_substr: str,
) -> None:
    """Validate each test pair against expected HIT/MISS decision and reason substring."""
    passed, reasons = compare(query_a, query_b, jaccard_threshold=0.60)
    decision = "HIT" if passed else "MISS"

    # Print detailed row so pytest -s / -v reports every pair clearly
    reasons_str = "; ".join(reasons) if reasons else "clean_pass"
    print(f"\n[GUARD TEST] {expected_decision} | Got: {decision} | Pair: {query_a!r} <-> {query_b!r} | Reasons: {reasons_str}")

    assert decision == expected_decision, (
        f"Mismatch for pair:\n  A: {query_a}\n  B: {query_b}\n"
        f"Expected: {expected_decision}, Got: {decision}\nReasons: {reasons}"
    )

    if expected_decision == "MISS":
        if expected_reason_substr == "lexical_steering":
            assert any(
                "content_tokens_mismatch" in r
                or "excess_absent_tokens" in r
                or "jaccard_below_threshold" in r
                for r in reasons
            ), f"Expected steering guard failure, got reasons: {reasons}"
        else:
            assert any(expected_reason_substr in r for r in reasons), (
                f"Expected reason containing {expected_reason_substr!r}, got reasons: {reasons}"
            )
    else:
        assert len(reasons) == 0, f"Expected clean pass for HIT pair, but got reasons: {reasons}"
