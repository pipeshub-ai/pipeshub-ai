"""The default content locator is the identity, and the locator for a search is built per search from the graph given to it."""
from __future__ import annotations

from app.modules.retrieval import content_locator
from app.modules.retrieval.content_locator import (
    SingleHolderLocator,
    locator_for,
    set_locator_factory,
)


async def test_the_default_locator_changes_nothing() -> None:
    locator = SingleHolderLocator()
    metadata = {"virtualRecordId": "v", "blockIndex": 3}
    hits = [{"metadata": metadata}]

    assert locator.internal_payload_keys == ()
    assert locator.stamp_hit(metadata) is metadata
    assert metadata == {"virtualRecordId": "v", "blockIndex": 3}
    assert await locator.filter_live(hits) is hits
    assert await locator.owners_of(["v"]) == {}
    assert await locator.holds_several(["v"]) is False
    assert locator.is_address(0) is False
    assert locator.is_address(1 << 40) is False
    assert locator.public_index(None, 7) == 7
    assert locator.public_index(2, 7) == 7
    assert locator.decode_ref(12, "r", None) == (0, 12)
    assert locator.display_number({}, 0) == 1
    assert locator.display_number({}, 4) == 5
    assert await locator.describe({}, "v", None) is None


def test_the_factory_gets_the_graph_of_each_call_and_nothing_is_kept() -> None:
    graphs: list[object] = []

    class Mine(SingleHolderLocator):
        pass

    def factory(graph: object) -> Mine:
        graphs.append(graph)
        return Mine()

    set_locator_factory(factory)
    first, second = object(), object()

    one, two = locator_for(first), locator_for(second)

    assert graphs == [first, second]
    assert one is not two


def test_the_default_factory_builds_a_new_locator_for_every_search() -> None:
    assert isinstance(locator_for(object()), SingleHolderLocator)
    assert locator_for(object()) is not locator_for(object())


def test_installing_the_same_factory_twice_is_a_no_op() -> None:
    def factory(graph: object) -> SingleHolderLocator:
        return SingleHolderLocator()

    set_locator_factory(factory)
    set_locator_factory(factory)

    assert isinstance(locator_for(None), SingleHolderLocator)


def test_the_reset_restores_the_default() -> None:
    class Mine(SingleHolderLocator):
        internal_payload_keys = ("x",)

    set_locator_factory(lambda graph: Mine())
    assert locator_for(None).internal_payload_keys == ("x",)

    content_locator.reset_locator_factory()

    assert locator_for(None).internal_payload_keys == ()


def test_a_locator_installed_by_one_test_is_gone_in_the_next_one_part_1() -> None:
    set_locator_factory(lambda graph: None)  # type: ignore[arg-type, return-value]


def test_a_locator_installed_by_one_test_is_gone_in_the_next_one_part_2() -> None:
    assert isinstance(locator_for(None), SingleHolderLocator)
