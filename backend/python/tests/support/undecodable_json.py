"""Message bodies that json.loads can never decode, yet does not reject with a JSONDecodeError (F34).

Every consumer treats a JSONDecodeError as poison. These raise RecursionError (nesting past the decoder's fixed
limit, about 10 000 levels) or a plain ValueError (an integer past ``sys.get_int_max_str_digits()``, 4300 digits).
"""
import json

DEEP = "[" * 100_000 + "]" * 100_000
LONG_INT = '{"eventType": "newRecord", "payload": {"n": ' + "1" * 5000 + "}}"
UNDECODABLE = {
    "nesting_too_deep": DEEP,
    "nesting_too_deep_double_encoded": json.dumps(DEEP),
    "nesting_too_deep_in_the_payload_string": json.dumps({"eventType": "newRecord", "payload": DEEP}),
    "integer_too_long": LONG_INT,
    "integer_too_long_in_the_payload_string": json.dumps({"eventType": "newRecord", "payload": LONG_INT}),
}
