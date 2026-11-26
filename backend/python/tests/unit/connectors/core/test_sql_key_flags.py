from app.connectors.core.sql.key_flags import (
    KeyDefinition,
    KeyFlags,
    key_flags,
    primary_key_columns,
)


def flags_of(columns, *keys) -> dict:
    return key_flags(columns, keys)


def test_single_column_primary_key_is_unique_too() -> None:
    flags = flags_of(["id", "name"], KeyDefinition("primary", ("id",)))
    assert flags["id"] == KeyFlags(is_primary_key=True, is_unique=True)
    assert flags["name"] == KeyFlags()


def test_composite_primary_key_members_are_not_unique() -> None:
    flags = flags_of(["a", "b"], KeyDefinition("primary", ("a", "b")))
    expected = KeyFlags(is_primary_key=True, is_part_of_composite_primary_key=True)
    assert flags["a"] == flags["b"] == expected


def test_single_column_unique_key() -> None:
    assert flags_of(["code"], KeyDefinition("unique", ("code",)))["code"] == KeyFlags(is_unique=True)


def test_composite_unique_key() -> None:
    flags = flags_of(["a", "b"], KeyDefinition("unique", ("a", "b")))
    assert flags["a"] == flags["b"] == KeyFlags(is_part_of_composite_unique=True)


def test_a_column_in_a_composite_primary_key_and_its_own_unique_key() -> None:
    flags = flags_of(
        ["a", "b"], KeyDefinition("primary", ("a", "b")), KeyDefinition("unique", ("a",)),
    )
    assert flags["a"] == KeyFlags(is_primary_key=True, is_unique=True, is_part_of_composite_primary_key=True)


def test_a_key_naming_an_unknown_column_is_ignored_for_it() -> None:
    flags = flags_of(["a"], KeyDefinition("primary", ("a", "gone")), KeyDefinition("unique", ("gone",)))
    assert flags == {"a": KeyFlags(is_primary_key=True, is_part_of_composite_primary_key=True)}


def test_blank_names_and_empty_keys_are_skipped() -> None:
    assert flags_of(["", "a"], KeyDefinition("primary", ())) == {"a": KeyFlags()}


def test_a_repeated_key_changes_nothing() -> None:
    key = KeyDefinition("unique", ("a",))
    assert flags_of(["a"], key, key) == flags_of(["a"], key)


def test_primary_key_columns_in_key_order() -> None:
    keys = [KeyDefinition("unique", ("x",)), KeyDefinition("primary", ("b", "a"))]
    assert primary_key_columns(keys) == ["b", "a"]
    assert primary_key_columns([KeyDefinition("unique", ("x",))]) == []
