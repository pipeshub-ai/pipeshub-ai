# pyright: ignore-file

"""
Zammad on the simulator – knowledge-base answers
================================================

These sync with a token that holds knowledge_base.editor (the integration token holds no knowledge_base permission,
so the knowledge base lists it no answer). An answer's state follows Zammad's state machine: the latest timestamp that
has passed decides, so archived wins over published and a publication scheduled for later does not count yet. A
published answer is everyone's; an internal one belongs to the roles with reader or editor access to its category (a
role without a permission row has the access its knowledge_base.reader or .editor permission gives); a draft or
archived one belongs to the editor roles.

  TC-ZMSIM-KB-001  First sync of a stock install (no permission rows): published, internal, draft and archived
                   answers reach exactly their readers [F-ZAMMAD-KB-ROLE-DEFAULTS]
  TC-ZMSIM-KB-002  An answer archived after it was published is the editors' only; a published draft is everyone's;
                   an answer moved to another category follows it [F-ZAMMAD-KB-ARCHIVED]
  TC-ZMSIM-KB-003  A publication and an archiving scheduled for later do not count yet [F-ZAMMAD-KB-SCHEDULED]
  TC-ZMSIM-KB-004  Category permission rows decide role access: a row of none on a category takes its internal
                   answers from that role
  TC-ZMSIM-KB-005  A deleted answer leaves PipesHub with its attachment
"""

import pytest
from connectors.zammad.sim_instances import KB_TOKEN, base_instance
from connectors.zammad.sim_world import SETTLE_TIMEOUT_SEC, ZammadWorld, succeeded
from helper.graph_provider_utils import async_poll_until

LATER = 2 * 3600


@pytest.mark.integration
@pytest.mark.zammad_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZammadSimKnowledgeBase:

    async def test_tc_zmsim_kb_001_first_sync_answer_states(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(base_instance(), token=KB_TOKEN))
        await w.assert_matches_source("after the first sync of a published, an internal, a draft and an archived answer")

    async def test_tc_zmsim_kb_002_state_changes(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(base_instance(), token=KB_TOKEN))
        w.ops({"op": "archive_answer", "answer": "pub"},
              {"op": "publish_answer", "answer": "draft"},
              {"op": "update_answer", "answer": "int", "data": {"title": "Escalation rules (v2)", "category": "faq"}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after archiving a published answer, publishing a draft and moving an answer")

    async def test_tc_zmsim_kb_003_scheduled_states(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(base_instance(), token=KB_TOKEN))
        # The simulator's clock jumps ahead, the changes are stamped then, and it comes back: the timestamps are set
        # but have not passed, as when an editor schedules them.
        w.ops({"op": "advance", "seconds": LATER},
              {"op": "publish_answer", "answer": "int"},
              {"op": "archive_answer", "answer": "pub"},
              {"op": "advance", "seconds": -LATER})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a publication and an archiving were scheduled two hours ahead")

    async def test_tc_zmsim_kb_004_category_permission_rows(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        await w.start(base_instance(), token=KB_TOKEN, enable=False)
        w.ops({"op": "set_kb_permission", "target": "staff", "role": "Agent", "access": "none"},
              {"op": "set_kb_permission", "target": "faq", "role": "Agent", "access": "reader"},
              {"op": "set_kb_permission", "target": "faq", "role": "Admin", "access": "editor"})
        assert succeeded(await w.enable())
        await w.assert_matches_source("after a first sync with category permission rows")

    async def test_tc_zmsim_kb_005_deleted_answer(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(base_instance(), token=KB_TOKEN))
        attachment = f"kb_answer_{w.key('answer', 'pub')}_attachment_"

        async def synced() -> bool:
            return any(str(r["externalRecordId"]).startswith(attachment) for r in await w.records())

        # Without it, its removal below would prove nothing.
        await async_poll_until(synced, timeout=SETTLE_TIMEOUT_SEC, interval=5,
                               description="the published answer's attachment record")
        w.ops({"op": "delete_answer", "answer": "pub"}, {"op": "delete_answer", "answer": "draft"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a published and a draft answer were deleted")
