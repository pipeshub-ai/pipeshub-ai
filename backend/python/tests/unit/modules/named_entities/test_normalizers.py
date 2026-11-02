from datetime import UTC, datetime

import pytest

from app.modules.named_entities.domain.kinds import EntityKind, default_enabled_kinds
from app.modules.named_entities.extractor import _deterministic
from app.modules.named_entities.merge import resolve_overlaps
from app.modules.named_entities.normalizers.contacts import normalize_contact
from app.modules.named_entities.normalizers.dates import (
    NormalizationContext,
    normalize_temporal,
)
from app.modules.named_entities.normalizers.money import normalize_money
from app.modules.named_entities.normalizers.quantities import (
    normalize_percent,
    normalize_quantity,
)
from app.modules.named_entities.recognizers.pattern import (
    PatternRecognizer,
    is_suppressed_secret,
)
from app.modules.named_entities.recognizers.values import ValueCandidateRecognizer
from app.modules.named_entities.text import TextUnit


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def test_quarter_is_half_open():
    value = normalize_temporal(EntityKind.DATE_RANGE, "Q3 FY25", NormalizationContext(tz="UTC"))
    assert value is not None
    assert value.start_ms == _ms(2025, 7, 1)
    assert value.end_ms == _ms(2025, 10, 1)
    assert value.ambiguous is False


def test_day_month_without_year_has_no_range():
    value = normalize_temporal(EntityKind.DATE, "12 April", NormalizationContext(tz="UTC"))
    assert value is not None
    assert value.ambiguous is True
    assert value.start_ms is None
    assert value.end_ms is None


def test_next_friday_is_anchored_to_the_record():
    # Thursday 1 Jan 2026. The next Friday is 2 Jan.
    reference = _ms(2026, 1, 1)
    value = normalize_temporal(
        EntityKind.DATE,
        "next Friday",
        NormalizationContext(reference_time_ms=reference, tz="UTC"),
    )
    assert value is not None
    assert value.anchored is True
    assert value.start_ms == _ms(2026, 1, 2)


@pytest.mark.parametrize(
    ("surface", "granularity", "start", "end"),
    [
        # Tuesday 10 Mar 2026.
        ("last week", "week", (2026, 3, 2), (2026, 3, 9)),
        ("this month", "month", (2026, 3, 1), (2026, 4, 1)),
        ("next quarter", "quarter", (2026, 4, 1), (2026, 7, 1)),
        ("this year", "year", (2026, 1, 1), (2027, 1, 1)),
        ("FY2025", "year", (2025, 1, 1), (2026, 1, 1)),
        ("15 May 2026", "day", (2026, 5, 15), (2026, 5, 16)),
        ("May 15 2026", "day", (2026, 5, 15), (2026, 5, 16)),
    ],
)
def test_periods_cover_the_whole_period(surface, granularity, start, end):
    ctx = NormalizationContext(reference_time_ms=_ms(2026, 3, 10), tz="UTC")
    value = normalize_temporal(EntityKind.DATE_RANGE, surface, ctx)
    assert value is not None
    assert (value.granularity, value.start_ms, value.end_ms) == (granularity, _ms(*start), _ms(*end))


def test_an_iso_interval_keeps_both_ends():
    value = normalize_temporal(EntityKind.DATE_RANGE, "2024-01-01/2024-03-31", NormalizationContext(tz="UTC"))
    assert value is not None
    assert (value.start_ms, value.end_ms) == (_ms(2024, 1, 1), _ms(2024, 4, 1))
    assert value.timex == "2024-01-01/2024-03-31"


def test_a_backwards_iso_interval_is_not_a_range():
    assert normalize_temporal(EntityKind.DATE_RANGE, "2024-03-31/2024-01-01", NormalizationContext(tz="UTC")) is None
    assert normalize_temporal(EntityKind.DATE, "2024-01-01 and later", NormalizationContext(tz="UTC")) is None


def test_an_explicit_utc_offset_wins_over_the_deployment_zone():
    value = normalize_temporal(
        EntityKind.DATE_TIME, "2024-01-01T10:00:00Z", NormalizationContext(tz="America/New_York"),
    )
    assert value is not None
    assert value.start_ms == int(datetime(2024, 1, 1, 10, tzinfo=UTC).timestamp() * 1000)


def test_a_slash_date_still_reaches_the_fallback_parser():
    pytest.importorskip("dateparser")
    value = normalize_temporal(EntityKind.DATE, "03/15/2026", NormalizationContext(tz="UTC"))
    assert value is not None
    assert value.start_ms == _ms(2026, 3, 15)


@pytest.mark.parametrize(
    ("text", "surface", "kind"),
    [
        ("Revenue grew 12.5% to plan.", "12.5%", EntityKind.PERCENTAGE),
        ("Rate is 0.75 percent now.", "0.75 percent", EntityKind.PERCENTAGE),
        ("Ship by 15 May 2026, please.", "15 May 2026", EntityKind.DATE),
        ("Plan for FY2025 is set.", "FY2025", EntityKind.DATE_RANGE),
        ("It costs about 800 dollars.", "800 dollars", EntityKind.CURRENCY),
    ],
)
def test_value_recognizer_proposes_the_whole_span(text, surface, kind):
    mentions = ValueCandidateRecognizer(default_enabled_kinds()).recognize([TextUnit(0, "b", text)])
    assert [(m.surface, m.kind) for m in mentions] == [(surface, kind)]


@pytest.mark.parametrize(
    ("left", "right"),
    [("$1,250", "USD 1,250.00"), ("12.5%", "12.50%"), ("5 km", "5000 m")],
)
def test_equal_values_share_one_key(left, right):
    from app.modules.named_entities.mentions import RawMention
    from app.modules.named_entities.merge import mentions_to_entities

    def key(surface: str) -> str:
        kind = ValueCandidateRecognizer(default_enabled_kinds()).recognize([TextUnit(0, "b", surface)])[0].kind
        raw = RawMention(kind=kind, surface=surface, block_index=0, block_id="b", char_start=0,
                         char_end=len(surface), extractor="value")
        return mentions_to_entities([raw], NormalizationContext())[0].norm_key

    assert key(left) == key(right)


def test_version_is_not_money():
    assert normalize_money("v1.2") is None
    assert normalize_money("1.2") is None


def test_scaled_dollar_amount_is_usd_and_ambiguous():
    value = normalize_money("$1.2M")
    assert value is not None
    assert value.currency == "USD"
    assert value.amount_float == 1_200_000
    assert value.currency_ambiguous is True


@pytest.mark.parametrize(
    ("surface", "amount", "currency", "ambiguous"),
    [
        ("EUR 1.250,00", 1250, "EUR", False),
        ("1,250.00 USD", 1250, "USD", False),
        ("€1.250,50", 1250.5, "EUR", False),
        ("₹ 5,000", 5000, "INR", False),
        ("CHF 99.50", 99.5, "CHF", False),
        ("¥300", 300, "JPY", True),
        ("$2.5bn", 2_500_000_000, "USD", True),
    ],
)
def test_money_table(surface, amount, currency, ambiguous):
    value = normalize_money(surface)
    assert value is not None
    assert value.amount_float == amount
    assert value.currency == currency
    assert value.currency_ambiguous is ambiguous


def test_unknown_code_is_not_money():
    assert normalize_money("ABC 100") is None


@pytest.mark.parametrize(
    "text",
    ["Budget is EUR 1.250,00 total", "We owe 1,250.00 USD", "Paid $1.2M upfront", "Fee ₹ 5,000"],
)
def test_money_recognizer_proposes_the_whole_span(text):
    mentions = ValueCandidateRecognizer(frozenset({EntityKind.CURRENCY})).recognize(
        [TextUnit(block_index=0, block_id="b", text=text)]
    )
    assert len(mentions) == 1
    assert normalize_money(mentions[0].surface) is not None


def test_money_recognizer_does_not_join_separate_numbers():
    mentions = ValueCandidateRecognizer(frozenset({EntityKind.CURRENCY})).recognize(
        [TextUnit(block_index=0, block_id="b", text="$5 10 items")]
    )
    assert [m.surface for m in mentions] == ["$5"]


def _money_mentions(text: str) -> list[tuple[str, str]]:
    mentions = ValueCandidateRecognizer(frozenset({EntityKind.CURRENCY})).recognize([TextUnit(0, "b", text)])
    found = []
    for mention in mentions:
        value = normalize_money(mention.surface)
        found.append((mention.surface, value and f"{value.currency}:{value.amount}"))
    return found


@pytest.mark.parametrize(
    "text",
    ["Paid ₹2,50,000,000 today", "Paid Rs 1,23,456,789 today", "Net ₹12,34,56,789.123 paid", "Paid ₹1,23,4567"],
)
def test_a_malformed_grouping_gives_no_amount(text) -> None:
    """Better no value than the leading "₹2,50" read as 2.50 rupees."""
    assert _money_mentions(text) == []


@pytest.mark.parametrize(
    ("text", "surface", "key"),
    [
        ("Paid ₹2,50,000 today", "₹2,50,000", "INR:250000"),
        ("Fee Rs. 1,85,297 due", "Rs. 1,85,297", "INR:185297"),
        ("Budget INR 12,34,56,789", "INR 12,34,56,789", "INR:123456789"),
        ("Total 2,50,000 INR", "2,50,000 INR", "INR:250000"),
        ("Owed 1,85,297 rupees", "1,85,297 rupees", "INR:185297"),
        ("Net ₹2,50,000.50 paid", "₹2,50,000.50", "INR:250000.50"),
        ("Net ₹10,00,000.00 paid", "₹10,00,000.00", "INR:1000000.00"),
        ("Rs 2,50,000/- only", "Rs 2,50,000", "INR:250000"),
        ("Capex ₹1,50,000 crore", "₹1,50,000 crore", "INR:1500000000000"),
        # Not only rupees: the grouping says how to read the digits.
        ("Quote $1,23,456 sent", "$1,23,456", "USD:123456"),
    ],
)
def test_indian_digit_grouping_is_read_whole(text, surface, key) -> None:
    """'₹2,50,000' used to be cut at '₹2,50' and stored as 2.50 rupees."""
    assert _money_mentions(text) == [(surface, key)]


def test_an_indian_grouped_range_gives_both_ends() -> None:
    assert _money_mentions("Budget ₹1,00,000-₹2,00,000") == [
        ("₹1,00,000", "INR:100000"), ("₹2,00,000", "INR:200000"),
    ]


@pytest.mark.parametrize(
    ("text", "surface", "key"),
    [
        ("Paid $1,250,000", "$1,250,000", "USD:1250000"),
        ("Paid €2,50", "€2,50", "EUR:2.50"),
        ("Budget is EUR 1.250,00 total", "EUR 1.250,00", "EUR:1250.00"),
        ("We owe 12,345 USD", "12,345 USD", "USD:12345"),
        ("Fee ₹25,000", "₹25,000", "INR:25000"),
        ("Fee ₹5 lakh", "₹5 lakh", "INR:500000"),
    ],
)
def test_thousands_and_decimal_groupings_are_unchanged(text, surface, key) -> None:
    assert _money_mentions(text) == [(surface, key)]


@pytest.mark.parametrize("text", ["Sold 1,50,000 ₹10 tickets", "Sold 1,500 ₹10 tickets"])
def test_a_symbol_that_starts_the_next_amount_is_not_a_suffix(text) -> None:
    assert _money_mentions(text) == [("₹10", "INR:10")]


def test_an_indian_amount_reaches_the_indexed_values() -> None:
    found = _deterministic([TextUnit(0, "b", "Invoice total ₹1,85,297")], default_enabled_kinds(), NormalizationContext())
    assert [(mention.kind, mention.surface) for mention in found] == [(EntityKind.CURRENCY, "₹1,85,297")]
    assert normalize_money(found[0].surface).amount == 185297


def _money_values(text: str) -> list[tuple[str, str | None]]:
    """Each money span with its value, a range as "CUR:low..high"."""
    found = []
    for mention in ValueCandidateRecognizer(frozenset({EntityKind.CURRENCY})).recognize([TextUnit(0, "b", text)]):
        value = normalize_money(mention.surface)
        key = value and f"{value.currency}:{value.amount:f}"
        if value and value.amount_high is not None:
            key += f"..{value.amount_high:f}"
        found.append((mention.surface, key))
    return found


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Swiss grouping, with either apostrophe.
        ("Fee CHF 1'250.50 due", [("CHF 1'250.50", "CHF:1250.50")]),
        ("Fee CHF 1’250.50 due", [("CHF 1’250.50", "CHF:1250.50")]),
        ("Total 1'250'000 CHF", [("1'250'000 CHF", "CHF:1250000")]),
        ("Q1'25 revenue $5", [("$5", "USD:5")]),
        ("it's 5 USD", [("5 USD", "USD:5")]),
        ("price='$500'", [("$500", "USD:500")]),
        # German scale words, with their dot.
        ("Umsatz € 2.5 Mio.", [("€ 2.5 Mio", "EUR:2500000.0")]),
        ("Umsatz 2,5 Mrd. €", [("2,5 Mrd. €", "EUR:2500000000.0")]),
        ("Umsatz 2,5 Mio. €", [("2,5 Mio. €", "EUR:2500000.0")]),
        ("Budget EUR 300 Tsd.", [("EUR 300 Tsd", "EUR:300000")]),
        # Lower-case codes after a number; codes that are words stay out.
        ("Paid 1500 usd", [("1500 usd", "USD:1500")]),
        ("Paid 200 eur", [("200 eur", "EUR:200")]),
        ("Paid 1500 USD", [("1500 USD", "USD:1500")]),
        ("Give it 5 try", []),
        ("Bought 3 pen", []),
        ("See 10 top", []),
        ("Sent 12 cad drawings", []),
    ],
)
def test_amounts_written_the_swiss_german_or_lower_case_way(text, expected) -> None:
    assert _money_values(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Deal size $5-10M", [("$5-10M", "USD:5000000..10000000")]),
        ("Deal size $5–10 million", [("$5–10 million", "USD:5000000..10000000")]),
        ("Deal size USD 5 to 10M", [("USD 5 to 10M", "USD:5000000..10000000")]),
        ("Deal size €2M-3M", [("€2M-3M", "EUR:2000000..3000000")]),
        # The symbol written again on the high end is still one range.
        ("Deal size €2M-€3M", [("€2M-€3M", "EUR:2000000..3000000")]),
        ("Deal size $5M - $10M", [("$5M - $10M", "USD:5000000..10000000")]),
        ("Deal size $500K-1M", [("$500K-1M", "USD:500000..1000000")]),
        ("Deal size $1.5-2bn", [("$1.5-2bn", "USD:1500000000.0..2000000000")]),
        ("Fee Rs. 5-10 lakh", [("Rs. 5-10 lakh", "INR:500000..1000000")]),
        ("Range USD 1'000-2'000k", [("USD 1'000-2'000k", "USD:1000000..2000000")]),
        # The currency after both ends.
        ("Cost 500-1000 EUR", [("500-1000 EUR", "EUR:500..1000")]),
        ("Cost 5 to 10 million dollars", [("5 to 10 million dollars", "USD:5000000..10000000")]),
        # Both ends name a currency, and not the same one: no range.
        ("Deal $5M - €10M", [("$5M", "USD:5000000"), ("€10M", "EUR:10000000")]),
        # A loss of a range runs between the negated ends.
        ("Loss -$5-10M", [("-$5-10M", "USD:-10000000..-5000000")]),
        # Without a shared scale each amount stands alone, as before.
        ("Price $5-10", [("$5", "USD:5")]),
        ("Price $5 - $10", [("$5", "USD:5"), ("$10", "USD:10")]),
        ("Call USD 555-1234", [("USD 555", "USD:555")]),
        ("Plan $5-10Mbps", [("$5", "USD:5")]),
        # A range that runs backwards is no range.
        ("Deal $10-5M", [("$10-5M", None)]),
        ("Deal $500-1M", [("$500-1M", None)]),
        # A span of years before a code is no amount: "The 2019-2021 USD figures".
        ("The 2019-2021 USD figures", [("2019-2021 USD", None)]),
        ("From 2024-2025 eur budget lines", [("2024-2025 eur", None)]),
        ("Budget 2000-2500 EUR", [("2000-2500 EUR", "EUR:2000..2500")]),
    ],
)
def test_a_money_range_keeps_both_ends_with_its_shared_scale(text, expected) -> None:
    """"$5-10M" was stored as $5: off by a million, and its high end lost."""
    assert _money_values(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Item: Widget, Price (INR): 500, Qty: 3", [("(INR): 500", "INR:500")]),
        ("Amount (USD): 1,250.00", [("(USD): 1,250.00", "USD:1250.00")]),
        ("Price(USD): 500", [("(USD): 500", "USD:500")]),
        ("Cost (usd): 5", [("(usd): 5", "USD:5")]),
        ("Cost ($): 5", [("($): 5", "USD:5")]),
        ("Revenue (USD): -50", [("(USD): -50", "USD:-50")]),
        ("Price (USD): 5-10M", [("(USD): 5-10M", "USD:5000000..10000000")]),
        ("Loss (USD): (1,200)", [("(USD): (1,200)", "USD:-1200")]),
        ("Discount (%): 5", []),
        # A scaled header is not read: its scale would be lost.
        ("Revenue (USD m): 5", []),
        ("Revenue (USD '000): 5", []),
        ("Revenue (USD) 500", []),
    ],
)
def test_a_currency_named_in_a_header_reaches_the_amount(text, expected) -> None:
    """A table row reads "Header: value", so a currency in the header never sat next to the number."""
    assert _money_values(text) == expected


def _money(surface: str) -> str | None:
    value = normalize_money(surface)
    if value is None:
        return None
    key = f"{value.currency}:{value.amount:f}"
    return key if value.amount_high is None else f"{key}..{value.amount_high:f}"


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        # A surface a model hands over is not always a bare amount: a later dash is no range.
        ("$500 - 20% discount", "USD:500"),
        ("$1,200 (2-3 units)", "USD:1200"),
        ("USD 500 on 2024-01-15", "USD:500"),
        ("€500 – 2025 budget", "EUR:500"),
        ("1-800-555-1234 USD", "USD:1"),
        ("USD 555-1234", "USD:555"),
        ("$5-10 per seat", "USD:5"),
        ("$500 - $20 discount", "USD:500"),
        # A range with the currency or scale on both ends, or in front of both.
        ("$5M - $10M", "USD:5000000..10000000"),
        ("€2M-€3M", "EUR:2000000..3000000"),
        ("1-2 million USD", "USD:1000000..2000000"),
        ("($5-10M)", "USD:-10000000..-5000000"),
        # The high end never borrows the low end's scale: "5k - 10 USD" runs backwards.
        ("5k - 10 USD", None),
        # A header: its sign survives, and one that is not just a currency gives nothing.
        ("(USD): (500)", "USD:-500"),
        ("(₹): 500", "INR:500"),
        ("(EUR): 2,5 Mio.", "EUR:2500000.0"),
        ("(USD m): 5", None),
        ("(USD '000): 5", None),
        ("(USD 000): 5", None),
    ],
)
def test_a_model_supplied_surface_reads_as_one_amount_unless_it_is_a_bare_range(surface, expected) -> None:
    assert _money(surface) == expected


@pytest.mark.parametrize(("text", "surface"), [
    ("{'growth': '12%', 'weight': '5 kg'}", ["12%", "5 kg"]),
    ("a margin of '12.5%'", ["12.5%"]),
])
def test_a_quoted_percent_or_quantity_is_still_read(text, surface) -> None:
    kinds = frozenset({EntityKind.DIMENSION, EntityKind.PERCENTAGE})
    assert sorted(m.surface for m in ValueCandidateRecognizer(kinds).recognize([TextUnit(0, "b", text)])) == sorted(surface)


def test_a_range_key_never_merges_with_its_low_end() -> None:
    from app.modules.named_entities.merge import entity_identity

    _, range_key, _ = entity_identity(EntityKind.CURRENCY, "$5-10M", NormalizationContext())
    _, exact_key, _ = entity_identity(EntityKind.CURRENCY, "$5M", NormalizationContext())
    assert (range_key, exact_key) == ("money:USD:5000000..10000000", "money:USD:5000000")


@pytest.mark.parametrize(
    ("surface", "unit", "si"),
    [("450 MWh", "mwh", 1.62e12), ("12 kWh", "kwh", 4.32e7), ("5 wh", "wh", 18_000.0), ("2 GWh", "gwh", 7.2e12)],
)
def test_energy_is_a_quantity_in_joules(surface, unit, si) -> None:
    mentions = ValueCandidateRecognizer(frozenset({EntityKind.DIMENSION})).recognize([TextUnit(0, "b", f"Used {surface} in May")])
    assert [m.surface for m in mentions] == [surface]
    value = normalize_quantity(surface)
    assert (value.unit, value.dimension, value.si_value) == (unit, "energy", si)


@pytest.mark.parametrize("text", ["WhatsApp 5 users", "Shipped 1'250 kg", "Margin 1'250%"])
def test_no_quantity_is_read_from_a_word_or_the_tail_of_a_grouped_number(text) -> None:
    kinds = frozenset({EntityKind.DIMENSION, EntityKind.PERCENTAGE})
    assert ValueCandidateRecognizer(kinds).recognize([TextUnit(0, "b", text)]) == []


def test_celsius_converts_to_kelvin():
    value = normalize_quantity("20 °C")
    assert value is not None
    assert value.si_unit == "K"
    assert abs(value.si_value - 293.15) < 1e-9


def test_basis_points_are_a_fraction():
    value = normalize_percent("300 bps")
    assert value is not None
    assert float(value.value) == 0.03


def test_kilometres_convert_to_metres():
    value = normalize_quantity("5 km")
    assert value is not None
    assert value.si_unit == "m"
    assert value.si_value == 5000


def test_a_speed_is_not_read_as_a_length():
    (mention,) = ValueCandidateRecognizer(frozenset({EntityKind.DIMENSION})).recognize(
        [TextUnit(0, "b", "Keep it under 60 km/h here.")]
    )
    assert mention.surface == "60 km/h"
    assert normalize_quantity(mention.surface).dimension == "speed"


@pytest.mark.parametrize("text", ["100M users", "2M downloads", "the 5G rollout", "a 10G link"])
def test_a_capital_m_or_g_is_not_a_unit(text):
    assert ValueCandidateRecognizer(frozenset({EntityKind.DIMENSION})).recognize([TextUnit(0, "b", text)]) == []


@pytest.mark.parametrize(("text", "surface"), [("run 5 m", "5 m"), ("add 3 g", "3 g"), ("drive 5 KM", "5 KM"), ("5 MI", "5 MI")])
def test_units_still_match(text, surface):
    (mention,) = ValueCandidateRecognizer(frozenset({EntityKind.DIMENSION})).recognize([TextUnit(0, "b", text)])
    assert mention.surface == surface


@pytest.mark.parametrize(
    "secret",
    ["GB82 WEST 1234 5698 7654 32", "DE89370400440532013000", "078-05-1120", "AB 12 34 56 C", "ABCDE1234F"],
)
def test_ibans_and_national_ids_are_suppressed(secret):
    assert is_suppressed_secret(f"ref {secret} end") is True
    mentions = PatternRecognizer(default_enabled_kinds() | {EntityKind.PHONE}).recognize(
        [TextUnit(block_index=0, block_id="b", text=f"ref {secret} end")]
    )
    assert mentions == []


def test_url_span_excludes_trailing_punctuation():
    text = "see https://example.com/a)."
    (mention,) = PatternRecognizer(frozenset({EntityKind.URL})).recognize(
        [TextUnit(block_index=0, block_id="b", text=text)]
    )
    assert text[mention.char_start : mention.char_end] == mention.surface == "https://example.com/a"


def test_card_number_is_suppressed():
    card = "4111111111111111"
    assert is_suppressed_secret(card) is True
    mentions = PatternRecognizer(default_enabled_kinds()).recognize(
        [TextUnit(block_index=0, block_id="b", text=f"pay {card} now")]
    )
    assert all(card not in mention.surface for mention in mentions)


@pytest.mark.parametrize("number", ["+999 123 456 7890", "999 123 456 7890"])
def test_a_phone_number_phonenumbers_rejects_still_has_one_key(number):
    assert normalize_contact(EntityKind.PHONE, number).canonical == "+9991234567890"


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("15m", None),
        ("15 m", ("length", 15.0, "m")),
        ("16GB", ("data_size", 16e9, "B")),
        ("2 TiB", ("data_size", 2 * 2**40, "B")),
        ("4k tokens", ("tokens", 4000.0, "token")),
        ("1.5M tokens", ("tokens", 1_500_000.0, "token")),
        ("128,000 tokens", ("tokens", 128_000.0, "token")),
        ("30 °C", ("temperature", 303.15, "K")),
    ],
)
def test_units_the_corpus_uses(surface, expected):
    value = normalize_quantity(surface)
    assert (value and (value.dimension, value.si_value, value.si_unit)) == expected


@pytest.mark.parametrize(
    ("surface", "iso", "seconds"),
    [("250ms", "PT0.25S", 0.25), ("15 ms", "PT0.015S", 0.015), ("30 sec", "PT30S", 30), ("5 mins", "PT5M", 300), ("2 hrs", "PT2H", 7200)],
)
def test_short_time_units_are_durations(surface, iso, seconds):
    value = normalize_temporal(EntityKind.DURATION, surface, NormalizationContext())
    assert (value.iso, value.seconds) == (iso, seconds)


def test_ambiguous_suffixes_and_decades_are_not_values():
    text = "p95 latency 250ms on 16GB nodes, 128,000 tokens per call; the 1990s ended 15m ago at 10s of sites."
    found = _deterministic([TextUnit(0, "b", text)], default_enabled_kinds(), NormalizationContext())
    assert [(mention.kind.value, mention.surface) for mention in found] == [
        ("duration", "250ms"), ("dimension", "16GB"), ("dimension", "128,000 tokens"),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "123 " * 10_000,
        "1" + ",234" * 10_000,
        "123 " * 10_000,
        ",".join(str(100 + i % 900) for i in range(10_000)),
        "a." * 20_000,
        "₹" + "1,23," * 20_000,
        "1" + ",23" * 10_000 + " INR",
        "May 1-" * 10_000,
        "1 May - " * 10_000,
        "2025-01-01-" * 10_000,
        "1" + "'000" * 20_000 + " x",
        "$1-" * 20_000,
        "$1M-$" * 20_000,
        "USD 1 to " * 20_000,
        "(USD): " * 20_000,
        "1-2-" * 20_000 + " EUR",
        "1." * 20_000,
        "H1 " * 20_000,
        "1H " * 20_000,
        "between March " * 5_000,
        "between March 5 and " * 5_000,
        "between 2026-01-01 and " * 5_000,
        "1-Jan-" * 20_000,
    ],
    ids=[
        "space-grouped", "comma-grouped", "nbsp-grouped", "numeric-csv", "dotted-no-at", "indian-prefix",
        "indian-suffix", "month-day-dashes", "day-month-dashes", "iso-dashes", "apostrophe-grouped",
        "money-range-dashes", "money-range-symbol-twice", "money-range-to", "header-currency", "trailing-range-dashes",
        "dotted-run", "half-run", "half-first-run", "between-run", "between-day-run", "between-iso-run", "day-mon-run",
    ],
)
def test_a_long_run_of_digits_or_dots_scans_in_linear_time(text):
    import time

    unit = TextUnit(block_index=0, block_id="b", text=text)
    start = time.process_time()
    ValueCandidateRecognizer(frozenset(EntityKind)).recognize([unit])
    PatternRecognizer(frozenset(EntityKind)).recognize([unit])
    # The quadratic versions took minutes on these inputs; the linear ones take
    # about 30ms. CPU time and a wide margin keep xdist workers sharing a CI
    # runner from failing it on wall-clock stalls.
    assert time.process_time() - start < 5.0


@pytest.mark.parametrize(
    ("text", "surface", "kind"),
    [
        ("growth of 1,000% YoY", "1,000%", EntityKind.PERCENTAGE),
        ("ran 1,500 km", "1,500 km", EntityKind.DIMENSION),
        ("a,$5,b", "$5", EntityKind.CURRENCY),
        ("1 234 567 EUR", "1 234 567 EUR", EntityKind.CURRENCY),
    ],
)
def test_a_grouped_number_is_read_whole(text, surface, kind):
    found = ValueCandidateRecognizer(frozenset({kind})).recognize([TextUnit(0, "b", text)])
    assert [m.surface for m in found] == [surface]


def test_grouped_percents_and_quantities_parse():
    assert normalize_percent("1,000%").value == 10
    assert normalize_quantity("1,500 km").si_value == 1_500_000
    assert normalize_percent("1,5 %") is None


def test_mention_offsets_index_the_block_text_as_stored():
    from app.models.blocks import Block, BlockType
    from app.modules.named_entities.text import text_units_from_blocks

    block = Block(type=BlockType.TEXT, data="\n   Contact jane@example.com today")
    (unit,) = text_units_from_blocks([block])
    (mention,) = PatternRecognizer(frozenset({EntityKind.EMAIL})).recognize([unit])
    assert block.data[mention.char_start:mention.char_end] == mention.surface == "jane@example.com"
    assert unit.block_index == 0


def _stored_values(text: str) -> list[str]:
    from app.modules.named_entities.merge import mentions_to_entities

    ctx = NormalizationContext(reference_time_ms=_ms(2026, 3, 10), tz="UTC")
    mentions = ValueCandidateRecognizer(frozenset(EntityKind)).recognize([TextUnit(0, "b", text)])
    return sorted(entity.norm_key for entity in mentions_to_entities(mentions, ctx))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("raised USD 5 million", ["money:USD:5000000"]),
        ("a $2 billion deal", ["money:USD:2000000000"]),
        ("₹5 crore", ["money:INR:50000000"]),
        ("₹5 lakh", ["money:INR:500000"]),
        ("$2.5 Bn", ["money:USD:2500000000"]),
        ("$5MM ARR", ["money:USD:5000000"]),
        ("Pay $5 100 times", ["money:USD:5"]),
        ("1 250 € net", ["money:EUR:1250"]),
        ("TOP 10 accounts", []),
        ("CAD 3D drawings", []),
        ("ALL 100 EMPLOYEES", []),
        ("CAD 1,200", ["money:CAD:1200"]),
        ("refund of -$500", ["money:USD:-500"]),
        ("a ($1,200) loss", ["money:USD:-1200"]),
        ("margin fell -3% YoY", ["pct:-0.03"]),
        ("HK$500", ["money:HKD:500"]),
        ("RMB 100", ["money:CNY:100"]),
        ("42 years old", ["qty:age:42"]),
        ("speed of 10 m/s", []),
        ("a pre-2026 plan", []),
    ],
)
def test_the_value_stored_is_the_value_written(text, expected):
    assert _stored_values(text) == expected


def test_an_ordinal_day_is_a_day_not_its_month():
    (key,) = _stored_values("signed on 3rd March 2026")
    assert key.startswith("date:day:")


def test_an_amount_too_long_to_be_money_is_not_stored():
    assert normalize_money("$" + "9" * 400) is None


@pytest.mark.parametrize(("kind", "surface"), [
    (EntityKind.DATE, "9999-12-31"),
    (EntityKind.DATE, "December 9999"),
    (EntityKind.DATE_RANGE, "Q4 9999"),
    (EntityKind.DATE_TIME, "9999-12-31T23:59:59"),
    (EntityKind.URL, "https://[tenant].example.com"),
])
def test_a_span_whose_value_cannot_be_built_states_none(kind: EntityKind, surface: str) -> None:
    from app.modules.named_entities.normalizers import normalize_value

    assert normalize_value(kind, surface, NormalizationContext(tz="UTC")) is None


def test_an_out_of_range_date_does_not_fail_the_other_spans() -> None:
    text = "Valid until 9999-12-31, signed 2026-03-01."
    found = _deterministic([TextUnit(0, "b", text)], default_enabled_kinds(), NormalizationContext())
    assert [m.surface for m in found if m.kind is EntityKind.DATE] == ["2026-03-01"]


@pytest.mark.parametrize(
    ("text", "surface", "value"),
    [
        ("Sales tax (8.25%): $1,234.00", "8.25%", 0.0825),
        ("Sales tax (CA) (8.25%)", "8.25%", 0.0825),
        ("taxes, (8.25%)", "8.25%", 0.0825),
        ("| Q3 margin | (3.2%) |", "(3.2%)", -0.032),
        ("Margin\n(3.2%)", "(3.2%)", -0.032),
        ("Growth\t(4%)", "(4%)", -0.04),
        ("(8.25%) sales tax applies", "8.25%", 0.0825),
        ("|\u00a0(3.2%)\u00a0|", "(3.2%)", -0.032),
        ("Change (-5%) on last year", "-5%", -0.05),
        ("Churn fell -1.5% this quarter.", "-1.5%", -0.015),
    ],
)
def test_parentheses_after_a_word_are_an_aside_not_a_negative_percent(text, surface, value) -> None:
    (mention,) = ValueCandidateRecognizer(frozenset({EntityKind.PERCENTAGE})).recognize([TextUnit(0, "b", text)])
    assert mention.surface == surface
    assert float(normalize_percent(mention.surface).value) == pytest.approx(value)


def test_accounting_parentheses_still_make_money_negative() -> None:
    (mention,) = ValueCandidateRecognizer(frozenset({EntityKind.CURRENCY})).recognize(
        [TextUnit(0, "b", "Net loss ($1,200) for the year")]
    )
    assert mention.surface == "($1,200)"


@pytest.mark.parametrize(
    ("text", "surface", "kelvin"),
    [
        ("Rated for -25 °C cold starts.", "-25 °C", 248.15),
        ("Operating range 10-40 °C.", "40 °C", 313.15),
        ("Down to −40 °F at the pole.", "−40 °F", 233.15),
        ("Cold soak at −25°C overnight.", "−25°C", 248.15),
        ("Range -10–-40 °C.", "-40 °C", 233.15),
    ],
)
def test_a_temperature_keeps_its_minus_sign_but_a_range_dash_is_not_one(text, surface, kelvin) -> None:
    (mention,) = ValueCandidateRecognizer(frozenset({EntityKind.DIMENSION})).recognize([TextUnit(0, "b", text)])
    assert mention.surface == surface
    assert normalize_quantity(mention.surface).si_value == pytest.approx(kelvin)


def test_a_dash_joining_a_quarter_to_money_is_not_a_sign() -> None:
    (mention,) = ValueCandidateRecognizer(frozenset({EntityKind.CURRENCY})).recognize([TextUnit(0, "b", "Q3-$500 bonus")])
    assert mention.surface == "$500"


def test_a_spaced_or_doubled_minus_is_not_a_temperature_sign() -> None:
    assert normalize_quantity("- 25 °C") is None and normalize_quantity("--25 °C") is None
    (mention,) = ValueCandidateRecognizer(frozenset({EntityKind.DIMENSION})).recognize([TextUnit(0, "b", "Rated --25 °C")])
    assert mention.surface == "25 °C"


def test_a_dash_before_a_non_temperature_is_not_a_sign() -> None:
    (mention,) = ValueCandidateRecognizer(frozenset({EntityKind.DIMENSION})).recognize([TextUnit(0, "b", "Weights: -5 kg each")])
    assert mention.surface == "5 kg"
    assert normalize_quantity("-5 kg") is None and normalize_quantity("-20 tokens") is None


@pytest.mark.parametrize(
    ("surface", "timex", "start", "end"),
    [
        ("May 20–22, 2025", "2025-05-20/2025-05-22", (2025, 5, 20), (2025, 5, 23)),
        ("May 20-22, 2025", "2025-05-20/2025-05-22", (2025, 5, 20), (2025, 5, 23)),
        ("May 20 - 22, 2025", "2025-05-20/2025-05-22", (2025, 5, 20), (2025, 5, 23)),
        ("May 20th–22nd, 2025", "2025-05-20/2025-05-22", (2025, 5, 20), (2025, 5, 23)),
        ("from May 20 to 22, 2025", "2025-05-20/2025-05-22", (2025, 5, 20), (2025, 5, 23)),
        ("9–11 September 2025", "2025-09-09/2025-09-11", (2025, 9, 9), (2025, 9, 12)),
        ("9 to 11 September 2025", "2025-09-09/2025-09-11", (2025, 9, 9), (2025, 9, 12)),
        ("May 28 – June 3, 2025", "2025-05-28/2025-06-03", (2025, 5, 28), (2025, 6, 4)),
        ("28 May – 3 June 2025", "2025-05-28/2025-06-03", (2025, 5, 28), (2025, 6, 4)),
        ("May 20, 2025 – June 3, 2025", "2025-05-20/2025-06-03", (2025, 5, 20), (2025, 6, 4)),
        ("2025-09-09 to 2025-09-11", "2025-09-09/2025-09-11", (2025, 9, 9), (2025, 9, 12)),
        ("2025-09-09-2025-09-11", "2025-09-09/2025-09-11", (2025, 9, 9), (2025, 9, 12)),
        # A range that wraps the year takes the year it leaves out from the other end.
        ("Dec 28 – Jan 3, 2026", "2025-12-28/2026-01-03", (2025, 12, 28), (2026, 1, 4)),
        ("Dec 28, 2025 – Jan 3", "2025-12-28/2026-01-03", (2025, 12, 28), (2026, 1, 4)),
        ("Jan–Mar 2025", "2025-01/2025-03", (2025, 1, 1), (2025, 4, 1)),
        ("January to March 2025", "2025-01/2025-03", (2025, 1, 1), (2025, 4, 1)),
        ("Nov–Feb 2026", "2025-11/2026-02", (2025, 11, 1), (2026, 3, 1)),
        # Any backwards pair of months is read across the year end, as "Sep–Jun 2025" is a school year.
        ("Sep–Jun 2025", "2024-09/2025-06", (2024, 9, 1), (2025, 7, 1)),
        ("Mar 5 – Feb 3, 2025", "2024-03-05/2025-02-03", (2024, 3, 5), (2025, 2, 4)),
    ],
)
def test_a_date_range_keeps_both_ends(surface, timex, start, end) -> None:
    value = normalize_temporal(EntityKind.DATE_RANGE, surface, NormalizationContext(tz="UTC"))
    assert value is not None
    assert (value.timex, value.start_ms, value.end_ms) == (timex, _ms(*start), _ms(*end))
    assert value.anchor_source == "explicit"


def test_an_iso_datetime_range_keeps_its_times() -> None:
    value = normalize_temporal(
        EntityKind.DATE_RANGE, "2025-09-09T10:00 to 2025-09-09T12:30", NormalizationContext(tz="UTC"),
    )
    assert value is not None
    assert value.granularity == "minute"
    assert value.timex == "2025-09-09T10:00/2025-09-09T12:30"


@pytest.mark.parametrize(
    ("surface", "timex"),
    [("May 20–22", "XXXX-05-20/XXXX-05-22"), ("May 20    –    22", "XXXX-05-20/XXXX-05-22"), ("Dec 28 – Jan 3", "XXXX-12-28/XXXX-01-03"), ("Sep 9 – 11", "XXXX-09-09/XXXX-09-11")],
)
def test_a_range_with_no_year_is_not_given_one(surface, timex) -> None:
    # dateparser read the last day as the year: "May 20–22" was 2022-05-20.
    value = normalize_temporal(EntityKind.DATE_RANGE, surface, NormalizationContext(reference_time_ms=_ms(2026, 3, 10)))
    assert value is not None
    assert (value.timex, value.start_ms, value.ambiguous) == (timex, None, True)


@pytest.mark.parametrize(
    "surface",
    ["May 22–20, 2025", "May 22–20", "Feb 28–30, 2025", "3-1 May 2025", "May 22, 2026 – May 20, 2025", "May 20–Jun 2025"],
)
def test_a_backwards_or_impossible_range_has_no_value(surface) -> None:
    """Not handed on to dateparser, which made one day of it ("Feb 28–30, 2025" was Feb 28)."""
    assert normalize_temporal(EntityKind.DATE_RANGE, surface, NormalizationContext(tz="UTC")) is None


@pytest.mark.parametrize(("surface", "day"), [("May 20th, 2025", (2025, 5, 20)), ("3rd of June 2025", (2025, 6, 3))])
def test_an_ordinal_day_is_read_as_stated(surface, day) -> None:
    value = normalize_temporal(EntityKind.DATE, surface, NormalizationContext(tz="UTC"))
    assert value is not None
    assert (value.start_ms, value.anchor_source) == (_ms(*day), "explicit")


def _date_spans(text: str) -> list[tuple[str, str]]:
    found = _deterministic([TextUnit(0, "b", text)], default_enabled_kinds(), NormalizationContext(tz="UTC"))
    return [
        (mention.surface, mention.kind.value)
        for mention in resolve_overlaps(found)
        if mention.kind in {EntityKind.DATE, EntityKind.DATE_RANGE, EntityKind.DATE_TIME, EntityKind.DURATION}
    ]


@pytest.mark.parametrize(
    ("text", "surface"),
    [
        ("Held May 20–22, 2025 in Pune.", "May 20–22, 2025"),
        ("Held 9–11 September 2025 in Pune.", "9–11 September 2025"),
        ("The trip is May 28 – June 3, 2025.", "May 28 – June 3, 2025"),
        ("Valid May 20, 2025 – June 3, 2025 only.", "May 20, 2025 – June 3, 2025"),
        ("Q1 ran Jan–Mar 2025.", "Jan–Mar 2025"),
        ("Window 2025-09-09/2025-09-11 agreed.", "2025-09-09/2025-09-11"),
        ("Window 2025-09-09 to 2025-09-11 agreed.", "2025-09-09 to 2025-09-11"),
        ("Call 2025-09-09T10:00 to 2025-09-09T12:00 booked.", "2025-09-09T10:00 to 2025-09-09T12:00"),
        ("Office closed Dec 28 – Jan 3, 2026.", "Dec 28 – Jan 3, 2026"),
        ("Due Sept. 3–5, 2025.", "Sept. 3–5, 2025"),
    ],
)
def test_a_range_is_one_span(text, surface) -> None:
    assert _date_spans(text) == [(surface, "date_range")]


@pytest.mark.parametrize(
    ("text", "spans"),
    [
        # A score before a date is no range; the date still counts.
        ("Won 3-1 May 2025.", [("1 May 2025", "date")]),
        ("Meeting Sep 9 – 11:00 AM.", [("Sep 9", "date")]),
        ("Meeting May 3 – 5 PM.", [("May 3", "date")]),
        ("From May 3 to 5pm.", [("May 3", "date")]),
        ("Starts Jun 10-2025.", [("Jun 10", "date")]),
        ("May 2025 - 3 items shipped.", [("May 2025", "date")]),
        ("Signed May 20, 2025 and 2025-09-09.", [("May 20, 2025", "date"), ("2025-09-09", "date")]),
        ("Ship by 15 May 2026, please.", [("15 May 2026", "date")]),
        # A link takes part of the range; the date left outside it still counts.
        ("Logs at http://x.com/2025-09-09 to 2025-09-11 kept.", [("2025-09-11", "date")]),
    ],
)
def test_what_is_not_a_range_keeps_its_single_dates(text, spans) -> None:
    assert _date_spans(text) == spans


def test_a_range_reaches_the_indexed_values_whole() -> None:
    found = _deterministic(
        [TextUnit(0, "b", "Conference on 9–11 September 2025 in Pune.")], default_enabled_kinds(), NormalizationContext(tz="UTC"),
    )
    # The single date inside the range is not counted a second time.
    (mention,) = [m for m in found if m.kind in {EntityKind.DATE, EntityKind.DATE_RANGE}]
    assert mention.kind is EntityKind.DATE_RANGE
    value = normalize_temporal(mention.kind, mention.surface, NormalizationContext(tz="UTC"))
    assert value is not None
    assert (value.start_ms, value.end_ms) == (_ms(2025, 9, 9), _ms(2025, 9, 12))


def test_a_date_padded_with_spaces_normalizes_in_linear_time() -> None:
    import time

    # The single-date pattern takes any run of spaces ("Jan     5" in a padded table).
    start = time.process_time()
    normalize_temporal(EntityKind.DATE, "Jan" + " " * 16_000 + "5", NormalizationContext(tz="UTC"))
    assert time.process_time() - start < 1.0


def _dates(text: str) -> list[tuple[str, str | None]]:
    """Each date span with its value, through the recognizer and ``normalize_value``."""
    from app.modules.named_entities.normalizers import normalize_value

    kinds = frozenset({EntityKind.DATE, EntityKind.DATE_RANGE, EntityKind.DATE_TIME, EntityKind.DURATION})
    found = []
    for mention in ValueCandidateRecognizer(kinds).recognize([TextUnit(0, "b", text)]):
        value = normalize_value(mention.kind, mention.surface, NormalizationContext(tz="UTC"))
        found.append((mention.surface, value and (getattr(value, "timex", None) or value.iso)))
    return found


@pytest.mark.parametrize(("text", "expected"), [
    # Dotted dates are the European form, day first; dateparser read them month first.
    ("Due 10.09.2026 per contract", [("10.09.2026", "2026-09-10")]),
    ("Due 3.1.2026", [("3.1.2026", "2026-01-03")]),
    ("Due 2026.09.10", [("2026.09.10", "2026-09-10")]),
    ("Due 2026/09/10 per contract", [("2026/09/10", "2026-09-10")]),
    ("Due 31-Dec-2026 per contract", [("31-Dec-2026", "2026-12-31")]),
    # A two-digit year stays with dateparser, which keeps its century.
    ("Due 1-Jan-99", [("1-Jan-99", "1999-01-01T00:00:00+00:00")]),
    ("Revenue in H1 2026 grew", [("H1 2026", "2026-H1")]),
    ("Revenue in 1H26 grew", [("1H26", "2026-H1")]),
    ("Plan for H2 FY26", [("H2 FY26", "2026-H2")]),
    ("Plan for H2 fy26", [("H2 fy26", "2026-H2")]),
    ("H1'26 target", [("H1'26", "2026-H1")]),
    ("Plan for 2H 2026", [("2H 2026", "2026-H2")]),
    # A date that ends the sentence.
    ("Signed 10.09.2026.", [("10.09.2026", "2026-09-10")]),
    ("Released on 2026.09.10.", [("2026.09.10", "2026-09-10")]),
    ("between March and May 2026", [("between March and May 2026", "2026-03/2026-05"), ("May 2026", "2026-05")]),
    # Day and ISO ends, through extraction: one range span, the ends read as dates too.
    ("Audit between March 5 and May 10, 2026.", [
        ("between March 5 and May 10, 2026", "2026-03-05/2026-05-10"), ("March 5", "XXXX-03-05"),
        ("May 10, 2026", "2026-05-10"),
    ]),
    ("Audit between Jan 5 and 10, 2026", [("between Jan 5 and 10, 2026", "2026-01-05/2026-01-10"), ("Jan 5", "XXXX-01-05")]),
    ("Audit between 5 March and 10 May 2026", [
        ("between 5 March and 10 May 2026", "2026-03-05/2026-05-10"), ("5 March", "XXXX-03-05"),
        ("10 May 2026", "2026-05-10"),
    ]),
    ("Audit between 2026-01-01 and 2026-03-01", [
        ("between 2026-01-01 and 2026-03-01", "2026-01-01/2026-03-01"), ("2026-01-01", "2026-01-01"),
        ("2026-03-01", "2026-03-01"),
    ]),
    ("between March 2025 and May 2026", [
        ("between March 2025 and May 2026", "2025-03/2026-05"), ("March 2025", "2025-03"), ("May 2026", "2026-05"),
    ]),
])
def test_more_written_date_forms_read(text: str, expected: list[tuple[str, str | None]]) -> None:
    assert _dates(text) == expected


@pytest.mark.parametrize(("text", "expected"), [
    # Durations, not half-years.
    ("Meeting ran 1h 30 min", [("30 min", "PT30M")]),
    ("took 2h 15 minutes", [("15 minutes", "PT15M")]),
    ("H1\n2026", []),
    ("H2 20 units", []),
    ("H3 2026", []),
    ("H1N1 H2O H1B visa", []),
    ("NVIDIA H100 and H200 GPUs", []),
    ("part H120 and model H12026", []),
    ("Ouverture à 1H00", []),
    ("build 2026.09.10.4 and 10.09.2026.5", []),
    # Version strings and addresses.
    ("IntelliJ 2024.1.3", []),
    ("ip 192.168.10.2026", []),
    ("v1.2.2026.4", []),
    ("10.09.26", []),
    # Matched but no such day: no value.
    ("2026/13/40", [("2026/13/40", None)]),
    ("phone 12.34.5678", [("12.34.5678", None)]),
    # "and" splits a range only after "between".
    ("Smith and May 2026", [("May 2026", "2026-05")]),
    ("2024-01-01 and later", [("2024-01-01", "2024-01-01")]),
    ("between 3 and 5 people", []),
    ("between May 2 and noon", [("May 2", "XXXX-05-02")]),
])
def test_lookalikes_are_not_read_as_new_date_forms(text: str, expected: list[tuple[str, str | None]]) -> None:
    assert _dates(text) == expected


def test_a_half_year_is_a_six_month_range_kept_through_normalize_value() -> None:
    """"half" missing from the granularity type made every half-year fail validation, silently."""
    from app.modules.named_entities.normalizers import normalize_value

    value = normalize_value(EntityKind.DATE_RANGE, "H2 2026", NormalizationContext(tz="UTC"))
    assert value is not None and value.granularity == "half"
    assert (value.start_ms, value.end_ms) == (_ms(2026, 7, 1), _ms(2027, 1, 1))



@pytest.mark.parametrize(("surface", "timex", "end"), [
    # The end is exclusive: the day after the last day named.
    ("between March 5 and May 10, 2026", "2026-03-05/2026-05-10", (2026, 5, 11)),
    ("between Jan 5 and 10, 2026", "2026-01-05/2026-01-10", (2026, 1, 11)),
    ("between 2026-01-01 and 2026-03-01", None, (2026, 3, 2)),
])
def test_a_between_range_with_day_ends_reads_whole(surface: str, timex: str | None, end: tuple[int, int, int]) -> None:
    from app.modules.named_entities.normalizers import normalize_value

    value = normalize_value(EntityKind.DATE_RANGE, surface, NormalizationContext(tz="UTC"))
    assert value is not None and value.start_ms is not None
    assert value.end_ms == _ms(*end)
    if timex:
        assert value.timex == timex


def test_a_half_by_name_through_normalize_value_rejects_product_codes() -> None:
    from app.modules.named_entities.normalizers import normalize_value

    for surface in ("H100", "H200", "H12026", "1H00"):
        value = normalize_value(EntityKind.DATE_RANGE, surface, NormalizationContext(tz="UTC"))
        assert value is None or value.granularity != "half", surface



def test_a_between_span_padded_with_spaces_normalizes_in_linear_time() -> None:
    import time

    surface = "between March and May" + " " * 40_000 + "2026"
    start = time.process_time()
    value = normalize_temporal(EntityKind.DATE_RANGE, surface, NormalizationContext(tz="UTC"))
    # The " and " separator retried at every space took about 4 s here; guarded, milliseconds.
    assert time.process_time() - start < 1.0
    assert value is not None and value.timex == "2026-03/2026-05"
