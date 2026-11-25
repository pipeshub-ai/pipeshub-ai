"""WP99-UI (ADR-RUN-01 D4): the PipesHub engine presenter shows a turn's time plan as the chat's ``time_plan``
notice, valid against the event contract, with when writing starts at the latest and when the work must be done."""

from __future__ import annotations

from app.harness.contracts.generated.pipeshub.events.x_pipeshub_run_notice import (
    PipeshubRunNoticeData,
)
from app.harness.engines.presenter import PipesHubEngineRunPresenter
from harness_core.budget.run_budget import PhasePlan
from harness_core.engines.durable.ports import (
    ProgressStamp,
    ShowsTimePlan,
    SilentPresenter,
)

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z


def test_wp99_ui_1_the_presenter_shows_the_plan_as_the_chats_notice() -> None:
    presenter = PipesHubEngineRunPresenter()
    assert isinstance(presenter, ShowsTimePlan)
    assert not isinstance(SilentPresenter(), ShowsTimePlan)
    plan = PhasePlan(NOW, NOW + 1_530, NOW + 1_800, NOW + 3_600)
    [event] = presenter.planned(ProgressStamp("run-1", "trn_1", iter(range(10)).__next__), plan)
    assert (event.type, event.critical) == ("x.pipeshub.run.notice", False)
    assert event.data == {
        "code": "time_plan",
        "writeBy": "2027-01-15T08:25:30.000Z",
        "finishBy": "2027-01-15T08:30:00.000Z",
    }
    assert PipeshubRunNoticeData.model_validate(event.data).code == "time_plan"
