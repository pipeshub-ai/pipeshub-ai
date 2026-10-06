import json
from pathlib import Path

import pytest

from app.modules.agents import handles

_SLUG_CASES = json.loads((Path(__file__).parent / "slug_cases.json").read_text(encoding="utf-8"))["cases"]


class TestSlugify:
    @pytest.mark.parametrize(("name", "expected"), _SLUG_CASES)
    def test_ascii_slug(self, name: str, expected: str) -> None:
        assert handles.slugify(name) == expected

    @pytest.mark.parametrize("name", ["Assistant", "PipesHub", "AI", "Bot", "Everyone", "here", "ALL", "Agent"])
    def test_reserved_names_get_a_suffix(self, name: str) -> None:
        slug = handles.slugify(name)
        assert slug == f"{name.lower()}-agent"
        assert not handles.is_reserved(slug)

    def test_long_names_leave_room_for_a_suffix(self) -> None:
        slug = handles.slugify("a" * 200)
        assert len(slug) <= 37
        assert len(handles.next_candidate(slug, 99)) <= handles.MAX_LENGTH

    def test_truncation_never_leaves_a_trailing_hyphen(self) -> None:
        slug = handles.slugify("word " * 30)
        assert not slug.endswith("-")

    @pytest.mark.parametrize("name", ["Sales Bot", "🚀", "Assistant", "x", "Ünï", "a" * 90, "!!", "9 lives"])
    def test_result_always_satisfies_the_handle_pattern(self, name: str) -> None:
        slug = handles.slugify(name)
        assert handles.is_valid_format(slug) and not handles.is_reserved(slug)


class TestNextCandidate:
    def test_first_candidate_is_the_base(self) -> None:
        assert handles.next_candidate("offer-drafter", 1) == "offer-drafter"

    def test_suffixes_start_at_two(self) -> None:
        assert handles.next_candidate("offer-drafter", 2) == "offer-drafter-2"
        assert handles.next_candidate("offer-drafter", 99) == "offer-drafter-99"

    def test_past_99_raises(self) -> None:
        with pytest.raises(ValueError):
            handles.next_candidate("offer-drafter", 100)

    def test_a_forty_character_base_is_trimmed_to_fit(self) -> None:
        candidate = handles.next_candidate("a" * 40, 12)
        assert len(candidate) == 40 and candidate.endswith("-12")

    def test_trim_does_not_leave_a_double_hyphen(self) -> None:
        candidate = handles.next_candidate("a" * 36 + "-bcd", 2)
        assert "--" not in candidate and handles.is_valid_format(candidate)


class TestFirstFreeCandidate:
    def test_skips_taken(self) -> None:
        assert handles.first_free_candidate("sales-bot", {"sales-bot", "sales-bot-2"}) == "sales-bot-3"

    def test_start_skips_the_bare_base(self) -> None:
        assert handles.first_free_candidate("sales-bot", set(), start=2) == "sales-bot-2"

    def test_none_when_exhausted(self) -> None:
        taken = {handles.next_candidate("a-b", n) for n in range(1, 100)}
        assert handles.first_free_candidate("a-b", taken) is None


class TestFormat:
    @pytest.mark.parametrize("handle", ["ab", "a-b", "a1", "x" * 40, "0-0"])
    def test_valid(self, handle: str) -> None:
        assert handles.is_valid_format(handle)

    @pytest.mark.parametrize("handle", ["", "a", "Bad Handle", "UPPER", "a_b", "x" * 41, "é", "a.b", "a\n"])
    def test_invalid(self, handle: str) -> None:
        assert not handles.is_valid_format(handle)


def test_reserved_is_the_shared_alias_set() -> None:
    assert handles.RESERVED == {"pipeshub", "assistant", "agent", "ai", "bot", "everyone", "here", "all"}
