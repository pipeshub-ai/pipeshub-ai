"""Every extension point of the indexing service has a default that changes nothing, and the registry is reset between tests."""
from __future__ import annotations

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.indexing import extension
from app.modules.indexing.extension import (
    Admit,
    DefaultIndexingLifecycle,
    DefaultLongDocumentPolicy,
    DefaultRecordOwnership,
    IndexingExtension,
    LongDocument,
    Proceed,
    ReembedPlan,
    notify_terminal,
)


def _doc() -> LongDocument:
    return LongDocument(
        record_id="r1", snapshot={}, org_id="o1", content=b"", content_rev=None, md5=None, extension="pdf",
        mime_type="application/pdf",
    )


def test_the_default_extension_is_the_default_of_every_point() -> None:
    current = extension.current()

    assert isinstance(current.ownership, DefaultRecordOwnership)
    assert isinstance(current.policy, DefaultLongDocumentPolicy)
    assert isinstance(current.lifecycle, DefaultIndexingLifecycle)
    assert current.stage_hooks is None
    assert current.deferred_stages() == frozenset()


async def test_the_default_ownership_answers_for_the_record_itself() -> None:
    own = DefaultRecordOwnership()

    assert own.owner_connector_id({"connectorId": "c1"}) == "c1"
    assert own.owner_connector_id({"connectorId": ""}) is None
    assert own.owner_connector_id({}) is None
    assert own.owner_connector_id(None) is None

    class Model:
        connector_id = "c2"

    assert own.owner_connector_id(Model()) == "c2"
    assert own.is_derived({"connectorId": "c1"}) is False
    assert own.has_lane("anything") is True
    page = [{"_key": "a"}]
    assert await own.prime_readable(page, object(), None, None) is page
    assert await own.prime(page, object()) is None
    assert [p async for p in own.derived_pages(object(), "c", "o", {}, page_size=10)] == []


async def test_the_default_policy_lets_every_document_through() -> None:
    policy = DefaultLongDocumentPolicy()

    assert await policy.check(_doc()) == Proceed()
    assert await policy.holds_stand_in("v", "pdf", "application/pdf") is False
    assert policy.deferred_stages() == frozenset()


async def test_the_default_lifecycle_does_nothing() -> None:
    life = DefaultIndexingLifecycle()

    assert await life.admit({}, object()) == Admit()
    assert await life.after_parse({}, object(), None) is None
    assert await life.on_terminal({}, "COMPLETED") is None
    assert await life.on_stage_settled(object()) is None
    assert await life.on_reextract_complete({}, object()) is False
    assert await life.on_connector_purged("o", "c") is None
    assert await life.recovery_pass() == 0
    assert await life.reembed_plan({}) is None
    assert await life.skip_first_use({}) is False


def test_install_replaces_the_extension_and_reset_restores_the_default() -> None:
    mine = IndexingExtension()
    extension.install(mine)
    assert extension.current() is mine

    extension.reset()
    assert extension.current() is not mine


def test_the_extension_installed_by_one_test_is_gone_in_the_next_one_part_1() -> None:
    extension.install(IndexingExtension(stage_hooks=object()))  # type: ignore[arg-type]


def test_the_extension_installed_by_one_test_is_gone_in_the_next_one_part_2() -> None:
    assert extension.current().stage_hooks is None


async def test_notify_terminal_gives_the_lifecycle_the_status_value() -> None:
    seen: list[tuple[object, object]] = []

    class Life(DefaultIndexingLifecycle):
        async def on_terminal(self, record: object, status: object) -> None:
            seen.append((record, status))

    extension.install(IndexingExtension(lifecycle=Life()))
    record = {"_key": "r"}

    await notify_terminal(record, ProgressStatus.EMPTY)
    await notify_terminal(record, "FAILED")

    assert seen == [(record, "EMPTY"), (record, "FAILED")]


def test_a_stage_hooks_protocol_is_satisfied_by_a_full_implementation_only() -> None:
    from app.modules.indexing.extension import StageHooks

    class Full:
        async def classify_input(self, job: object, io: object) -> None: ...
        async def entities_input(self, job: object, io: object) -> None: ...
        async def save_classification(self, job: object, io: object, owner: object, metadata: object) -> bool:
            return False

        async def keep_extraction(self, job: object, io: object, owner: object, extraction: object) -> bool:
            return False

        async def restore_classification(self, job: object, io: object, metadata: object) -> bool:
            return False

    class Partial:
        async def classify_input(self, job: object, io: object) -> None: ...

    assert isinstance(Full(), StageHooks)
    assert not isinstance(Partial(), StageHooks)


def test_the_whole_file_reembed_plan_is_what_the_stored_block_reindex_always_did() -> None:
    plan = ReembedPlan.whole()

    assert plan == ReembedPlan()
    assert plan.delete_whole is True
    assert plan.reproject_entities is True
    assert dict(plan.settings) == {}
    assert plan.after is None


@pytest.mark.parametrize("status", [ProgressStatus.COMPLETED, ProgressStatus.FAILED])
async def test_notify_terminal_without_an_extension_never_raises(status: ProgressStatus) -> None:
    await notify_terminal({"_key": "r"}, status)
