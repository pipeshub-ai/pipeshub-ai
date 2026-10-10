import random
import time

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.graph_writer import edge_payload
from app.modules.named_entities.mentions import RawMention
from app.modules.named_entities.merge import (
    _PRIORITY,
    mentions_to_entities,
    resolve_overlaps,
)
from app.modules.named_entities.normalizers.dates import NormalizationContext

_EXTRACTORS = ["pattern", "value", "agent", "single_call"]


def _mention(block: int, start: int, end: int, extractor: str) -> RawMention:
    return RawMention(EntityKind.ORGANIZATION, "x", block, "b", start, end, extractor)  # type: ignore[arg-type]


def _pairwise(mentions: list[RawMention]) -> list[RawMention]:
    ordered = sorted(
        mentions, key=lambda m: (_PRIORITY.get(m.extractor, 9), -(m.char_end - m.char_start), m.char_start)
    )
    kept: list[RawMention] = []
    for m in ordered:
        if not any(
            m.block_index == k.block_index and m.char_start < k.char_end and k.char_start < m.char_end for k in kept
        ):
            kept.append(m)
    return kept


def test_matches_a_pairwise_check_on_random_spans():
    rng = random.Random(7)
    for _ in range(500):
        mentions = []
        for _ in range(rng.randint(0, 40)):
            start = rng.randint(0, 60)
            mentions.append(_mention(rng.randint(0, 2), start, start + rng.randint(0, 8), rng.choice(_EXTRACTORS)))
        assert resolve_overlaps(mentions) == _pairwise(mentions)


def test_the_same_span_in_another_block_is_kept():
    first, second = _mention(0, 0, 4, "value"), _mention(1, 0, 4, "value")
    assert resolve_overlaps([first, second]) == [first, second]


def test_many_mentions_resolve_quickly():
    mentions = [_mention(index % 50, (index // 50) * 3, (index // 50) * 3 + 2, "value") for index in range(50_000)]
    started = time.perf_counter()
    assert len(resolve_overlaps(mentions)) == 50_000
    assert time.perf_counter() - started < 5



def test_a_link_reaches_the_graph_without_its_credentials():
    surface = "https://bob:hunter2@Example.com/a?b=1&token=secret"
    raw = RawMention(EntityKind.URL, surface, 0, "b0", 0, len(surface), "pattern")
    (entity,) = mentions_to_entities([raw], NormalizationContext(tz="UTC"))
    assert entity.display_name == "https://example.com/a?b=1"
    names = edge_payload("org", entity, 1)["extractedNames"]
    assert names == ["https://example.com/a?b=1"]
    assert not any("hunter2" in name or "secret" in name for name in names)



def _raw(kind: EntityKind, surface: str, hint: str = "") -> RawMention:
    return RawMention(kind, surface, 0, "b0", 0, len(surface), "agent", normalized_hint=hint)


def test_the_value_comes_from_the_text_never_from_the_hint():
    ctx = NormalizationContext(reference_time_ms=1773100800000, tz="UTC")
    cases = [
        (EntityKind.CURRENCY, "$5", "USD 999", "money:USD:5"),
        (EntityKind.PERCENTAGE, "5%", "95%", "pct:0.05"),
        (EntityKind.DATE, "3 June 2026", "2027-01-01", None),
    ]
    for kind, surface, hint, expected in cases:
        (entity,) = mentions_to_entities([_raw(kind, surface, hint)], ctx)
        if expected:
            assert entity.norm_key == expected
        assert "2027" not in entity.norm_key and "999" not in entity.norm_key


def test_a_value_kind_whose_text_states_no_value_is_not_stored():
    ctx = NormalizationContext(tz="UTC")
    mentions = [_raw(EntityKind.CURRENCY, "Acme"), _raw(EntityKind.DATE, "the kickoff"), _raw(EntityKind.DATE, "3 days")]
    assert mentions_to_entities(mentions, ctx) == []


def test_a_secret_in_a_hint_is_not_kept():
    (entity,) = mentions_to_entities([_raw(EntityKind.ORGANIZATION, "Acme", "4111111111111111")], NormalizationContext(tz="UTC"))
    assert entity.normalized_raw == ""


def test_a_long_link_keeps_the_offsets_true_and_a_bounded_key():
    url = "https://example.com/" + "a" * 1500
    text = f"see {url} now"
    raw = RawMention(EntityKind.URL, url, 0, "b0", 4, 4 + len(url), "pattern")
    (entity,) = mentions_to_entities([raw], NormalizationContext(tz="UTC"))
    (mention,) = entity.mentions
    assert text[mention.char_start:mention.char_end] == mention.surface
    assert entity.norm_key.startswith("url:sha256:") and len(entity.norm_key) < 100


def test_a_span_too_long_to_be_an_entity_is_dropped():
    url = "https://example.com/" + "a" * 5000
    raw = RawMention(EntityKind.URL, url, 0, "b0", 0, len(url), "pattern")
    assert mentions_to_entities([raw], NormalizationContext(tz="UTC")) == []
