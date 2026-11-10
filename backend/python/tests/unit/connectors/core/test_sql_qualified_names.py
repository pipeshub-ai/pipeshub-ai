import pytest

from app.connectors.core.sql.qualified_names import (
    join_qualified_name,
    leading_parts,
    split_qualified_name,
)


class TestJoin:
    def test_plain_parts_join_with_dots(self) -> None:
        assert join_qualified_name(("DB", "SCHEMA", "T")) == "DB.SCHEMA.T"

    def test_part_with_a_dot_is_quoted(self) -> None:
        assert join_qualified_name(("DB", "a.b", "T")) == 'DB."a.b".T'

    def test_part_starting_with_a_quote_is_quoted_and_doubled(self) -> None:
        assert join_qualified_name(('"odd', "T")) == '"""odd".T'

    def test_quote_inside_a_plain_name_is_left_alone(self) -> None:
        assert join_qualified_name(('o"dd', "T")) == 'o"dd.T'

    def test_different_names_never_share_an_id(self) -> None:
        assert join_qualified_name(("a.b", "c")) != join_qualified_name(("a", "b.c"))


class TestSplit:
    @pytest.mark.parametrize(
        "parts",
        [
            ("T",),
            ("S", "T"),
            ("DB", "S", "T"),
            ("db", "my.schema", "t.with.dots"),
            ('"quoted"', 'he said "hi"', "x"),
            ("", "S", ""),
            ("Mixed Case", "Spaces Here", "ünïcode"),
        ],
    )
    def test_round_trip(self, parts) -> None:
        assert split_qualified_name(join_qualified_name(parts), len(parts)) == parts

    def test_wrong_part_count_raises(self) -> None:
        with pytest.raises(ValueError):
            split_qualified_name("DB.S.T", 2)
        with pytest.raises(ValueError):
            split_qualified_name("DB.S", 3)

    def test_unterminated_quote_raises(self) -> None:
        with pytest.raises(ValueError):
            split_qualified_name('DB."S.T', 2)

    def test_text_after_a_closing_quote_raises(self) -> None:
        with pytest.raises(ValueError):
            split_qualified_name('"S"x.T', 2)


class TestLeadingParts:
    def test_reads_the_head_of_a_longer_id(self) -> None:
        assert leading_parts("DB.S.ST/dir/f.csv", 2) == (("DB", "S"), ".ST/dir/f.csv")

    def test_quoted_and_slashed_names(self) -> None:
        fqn = join_qualified_name(["MY/DB", "a.b"]) + ".V"
        assert leading_parts(fqn, 2) == (("MY/DB", "a.b"), ".V")

    def test_exact_name_leaves_nothing(self) -> None:
        assert leading_parts("DB.S", 2) == (("DB", "S"), "")

    def test_too_few_parts_raise(self) -> None:
        with pytest.raises(ValueError):
            leading_parts("DB", 2)
