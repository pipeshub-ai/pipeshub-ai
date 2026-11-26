"""A SchemaValidationError's text names the collection, the field and the rule, never the rejected value.

Its text reaches log lines (sync_run.end, task.failed, every ``{e}`` handler above a graph write), so the
value it was raised for (a name, a phone number, an email) must not be in it. The value stays on
``original_error`` for a debugger.
"""

import pytest
from jsonschema import Draft7Validator
from jsonschema import ValidationError as JsonSchemaValidationError

from app.schema import node_validator
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError

V = NodeSchemaValidator()
NAME = "Ada Seeded-Lovelace"
EMAIL = "ada.seeded@example.invalid"
PHONE = "+1 555 0100 Seeded"


def _raised(method: str, collection: str, payload: dict) -> SchemaValidationError:
    with pytest.raises(SchemaValidationError) as caught:
        getattr(V, method)(collection, payload)
    return caught.value


# (method, collection, payload, seeded values, text the message keeps)
VALUE_CASES = [
    pytest.param("validate_node", "users", {"email": EMAIL, "businessPhones": PHONE}, [PHONE],
                 ["'businessPhones'", "is not of type 'array'"], id="type-string"),
    pytest.param("validate_node", "users", {"email": EMAIL, "kind": NAME}, [NAME],
                 ["'kind'", "is not one of ['human', 'service']"], id="enum"),
    pytest.param("validate_node", "users", {"email": 4155550100}, ["4155550100"],
                 ["'email'", "is not of type 'string'"], id="type-number"),
    pytest.param("validate_node_update", "users", {"fullName": ["Ada", "Seeded-Lovelace"]}, ["Seeded-Lovelace"],
                 ["'fullName'", "is not of type 'string'"], id="type-list-partial"),
    pytest.param("validate_node_update", "records", {"sourceTimestampOrigin": EMAIL}, [EMAIL],
                 ["'sourceTimestampOrigin'", "does not match"], id="pattern"),
    pytest.param("validate_node_update", "mails", {"to": [{"name": NAME, "address": EMAIL}]}, [NAME, EMAIL],
                 ["'to.0'", "is not of type 'string'"], id="nested-dict"),
    pytest.param("validate_edge_update", "permission", {"createdAtTimestamp": {"note": NAME}}, [NAME],
                 ["'createdAtTimestamp'", "is not of type 'number'"], id="edge"),
    pytest.param("validate_node_update", "records", {"p_salary": {"amount": NAME}}, [NAME],
                 ["'p_salary'", "value is not valid under any of the given schemas"], id="record-property-anyof"),
    pytest.param("validate_node_update", "records", {"p_tags": ["ok", 7777123]}, ["7777123"],
                 ["'p_tags.1'", "value is not of type 'string'"], id="record-property-list-item-absolute-path"),
    pytest.param("validate_node_update", "records", {"recordName": ""}, ["''"],
                 ["'recordName': value should be non-empty"], id="empty-string"),
    pytest.param("validate_node", "users", {"email": 0}, ["0 "], ["'email': value is not of type 'string'"], id="zero"),
    pytest.param("validate_node", "users", {"email": True}, ["True"], ["'email': value is not of type 'string'"], id="true"),
]


@pytest.mark.parametrize(("method", "collection", "payload", "seeds", "kept"), VALUE_CASES)
def test_the_text_leaves_the_rejected_value_out(method, collection, payload, seeds, kept) -> None:
    error = _raised(method, collection, payload)

    text = str(error)
    for seed in seeds:
        assert seed not in text
    assert f"Schema validation failed for collection '{collection}'" in text
    for fragment in kept:
        assert fragment in text


@pytest.mark.parametrize(("method", "collection", "payload", "seeds", "kept"), VALUE_CASES)
def test_the_value_stays_on_the_original_error_for_a_debugger(method, collection, payload, seeds, kept) -> None:
    error = _raised(method, collection, payload)

    assert error.collection == collection
    assert all(seed in error.original_error.message for seed in seeds)


def test_the_rule_reads_as_a_sentence_about_the_value() -> None:
    text = str(_raised("validate_node", "users", {"email": EMAIL, "businessPhones": PHONE}))

    assert text == "Schema validation failed for collection 'users': at 'businessPhones': value is not of type 'array'"


def _raise_first(schema: dict, payload: dict) -> str:
    error = next(Draft7Validator(schema).iter_errors(payload))
    with pytest.raises(SchemaValidationError) as caught:
        node_validator._raise("users", error)
    return str(caught.value)


def test_a_value_whose_digits_recur_in_the_rule_keeps_only_the_rule() -> None:
    # "1 is less than the minimum of 10": stripping the leading 1 leaves a 1 in "10", so the check cannot tell.
    text = _raise_first({"properties": {"n": {"minimum": 10}}}, {"n": 1})

    assert text == "Schema validation failed for collection 'users': at 'n': value fails 'minimum'"


def test_a_message_that_quotes_part_of_the_value_keeps_only_the_rule() -> None:
    # "Additional items are not allowed ('<item>' was unexpected)" quotes an item, not the whole list.
    text = _raise_first({"properties": {"tags": {"items": [{"type": "string"}], "additionalItems": False}}},
                        {"tags": ["ok", NAME]})

    assert NAME not in text
    assert text.endswith("at 'tags': value fails 'additionalItems'")


def test_a_missing_required_field_is_still_named() -> None:
    assert "'email' is a required property" in str(_raised("validate_node", "users", {"fullName": NAME}))


def test_an_unexpected_field_is_still_named_but_not_its_value() -> None:
    text = str(_raised("validate_node", "users", {"email": EMAIL, "bogus": NAME}))

    assert "'bogus' was unexpected" in text
    assert NAME not in text and EMAIL not in text


def test_a_message_that_quotes_the_value_past_its_start_falls_back_to_the_rule() -> None:
    # Not a wording jsonschema uses today for these schemas; a future one (or a format checker) may.
    error = JsonSchemaValidationError(f"expected a name, got {NAME!r} instead", instance=NAME, validator="format", path=["fullName"])

    with pytest.raises(SchemaValidationError) as caught:
        node_validator._raise("users", error)

    text = str(caught.value)
    assert NAME not in text
    assert "'fullName'" in text and "'format'" in text
