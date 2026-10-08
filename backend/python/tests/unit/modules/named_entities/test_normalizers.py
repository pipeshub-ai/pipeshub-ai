from datetime import UTC, datetime

import pytest

from app.modules.named_entities.domain.kinds import EntityKind, default_enabled_kinds
from app.modules.named_entities.extractor import _deterministic
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
    ],
    ids=["space-grouped", "comma-grouped", "nbsp-grouped", "numeric-csv", "dotted-no-at"],
)
def test_a_long_run_of_digits_or_dots_scans_in_linear_time(text):
    import time

    unit = TextUnit(block_index=0, block_id="b", text=text)
    start = time.perf_counter()
    ValueCandidateRecognizer(frozenset(EntityKind)).recognize([unit])
    PatternRecognizer(frozenset(EntityKind)).recognize([unit])
    # The quadratic versions took minutes on these inputs.
    assert time.perf_counter() - start < 1.0


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
