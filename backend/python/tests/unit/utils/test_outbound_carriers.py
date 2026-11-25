"""Outbound context carriers: a plug-in's fields travel with the request id, and nothing changes while none is registered."""

from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from app.utils import outbound_carriers
from app.utils import request_context as rc


class _Carrier:
    http_headers = ("X-Extra",)

    def __init__(self, fields: dict[str, Any] | None = None, *, fail: bool = False) -> None:
        self.fields = fields if fields is not None else {"x-extra": "1"}
        self.fail = fail
        self.accepted: list[Mapping[str, Any]] = []

    def inject(self, carrier: dict[str, Any], kind: str) -> None:
        carrier.update(self.fields)
        if self.fail:
            raise RuntimeError("carrier failed")

    def accept(self, message: Mapping[str, Any]) -> None:
        self.accepted.append(message)
        if self.fail:
            raise RuntimeError("carrier failed")


@pytest.fixture(autouse=True)
def _no_carriers() -> Iterator[None]:
    outbound_carriers.reset()
    yield
    outbound_carriers.reset()


def test_with_no_carrier_the_request_functions_answer_as_before() -> None:
    message = {"eventType": "x"}
    assert rc.inject_envelope(message) is message  # no context, no carrier: the same object, as before
    assert rc.inject_request_headers({"a": "b"}) == {"a": "b"}
    assert outbound_carriers.header_names() == ()


def test_a_carrier_adds_its_fields_to_headers_and_envelopes_and_never_changes_the_callers_object() -> None:
    outbound_carriers.register(_Carrier())
    headers, message = {"a": "b"}, {"eventType": "x"}
    assert rc.inject_request_headers(headers) == {"a": "b", "x-extra": "1"}
    assert rc.inject_envelope(message) == {"eventType": "x", "x-extra": "1"}
    assert (headers, message) == ({"a": "b"}, {"eventType": "x"})
    assert outbound_carriers.header_names() == ("x-extra",)


def test_a_carrier_cannot_replace_what_is_already_there_and_http_values_are_strings() -> None:
    outbound_carriers.register(_Carrier({rc.HEADER_REQUEST_ID: "forged", "authorization": "forged", "n": 1, "x-extra": "1"}))
    token = rc.set_context("root-1")
    try:
        out = rc.inject_request_headers({"authorization": "real"})
    finally:
        rc.reset_context(token)
    assert out == {"authorization": "real", rc.HEADER_REQUEST_ID: "root-1", "x-extra": "1"}
    assert rc.inject_envelope({"k": "v"})["n"] == 1  # an envelope is JSON: any value may travel


def test_a_carrier_that_raises_is_skipped_and_leaves_nothing_behind() -> None:
    outbound_carriers.register(_Carrier({"half": "written"}, fail=True))
    good = _Carrier()
    outbound_carriers.register(good)
    assert rc.inject_request_headers() == {"x-extra": "1"}
    assert rc.context_from_envelope({rc.ENVELOPE_REQUEST_ID: "root-1"}).root_id == "root-1"
    assert good.accepted == [{rc.ENVELOPE_REQUEST_ID: "root-1"}]


def test_unregistering_restores_the_behaviour_without_carriers() -> None:
    unregister = outbound_carriers.register(_Carrier())
    unregister()
    unregister()
    assert not outbound_carriers.registered()
    assert rc.inject_request_headers() == {}
