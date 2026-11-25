"""``_pgbool`` reads a stored document flag as ``coalesce((doc->>key)::boolean, false)`` reads it, and refuses what the cast refuses, so a flag the
database could not read never makes a deleted or stub record look live."""
from __future__ import annotations

import pytest

from app.services.graph_db.postgres.base import _pgbool

TRUE = ["t", "tr", "tru", "true", "TRUE", "True", "y", "ye", "yes", "YES", "on", "ON", "1", " true", "true ", "\t\n\r\v\f yes \t", True, 1]
FALSE = ["f", "fa", "fal", "fals", "false", "FALSE", "n", "no", "No", "of", "off", "OFF", "0", " off ", False, 0, None]
INVALID = ["", " ", "o", "tt", "truee", "yess", "nope", "maybe", "2", "-1", "01", "1.0", "t r", " true", "true ", " yes", "ｔｒｕｅ", "abc",
           2, -1, 1.0, 0.0, 1.5, [], [True], {}, {"a": 1}]


@pytest.mark.parametrize("v", TRUE)
def test_the_forms_postgresql_reads_as_true(v: object) -> None:
    assert _pgbool(v) is True


@pytest.mark.parametrize("v", FALSE)
def test_the_forms_postgresql_reads_as_false_and_null_as_false(v: object) -> None:
    assert _pgbool(v) is False


@pytest.mark.parametrize("v", INVALID)
def test_anything_else_raises_as_the_cast_does(v: object) -> None:
    with pytest.raises(ValueError, match="not a boolean"):
        _pgbool(v)
