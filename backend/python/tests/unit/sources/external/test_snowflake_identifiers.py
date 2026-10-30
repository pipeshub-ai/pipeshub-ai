from app.sources.external.snowflake.identifiers import (
    qualified_identifier,
    quote_identifier,
    quote_literal,
    stage_reference,
)


def test_identifier_is_wrapped_and_inner_quotes_doubled() -> None:
    assert quote_identifier('a"b') == '"a""b"'


def test_identifier_keeps_case_and_spaces() -> None:
    assert quote_identifier("My Table") == '"My Table"'


def test_qualified_identifier_quotes_every_part() -> None:
    assert qualified_identifier("db", "s.x", "T") == '"db"."s.x"."T"'


def test_literal_doubles_quotes_and_backslashes() -> None:
    assert quote_literal("it's a\\path") == "'it''s a\\\\path'"


def test_stage_reference() -> None:
    assert stage_reference("DB", "S", "ST") == '@"DB"."S"."ST"'


def test_injection_in_a_name_stays_inside_one_literal() -> None:
    literal = quote_literal("x'; DROP TABLE t; --")
    assert literal == "'x''; DROP TABLE t; --'"
    # Only the outer quotes are unescaped.
    inner = literal[1:-1]
    assert "'" not in inner.replace("''", "")
