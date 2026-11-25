"""CTL-5: suggested next questions come with the agent harness; the retired Labs switch is not read anywhere."""

from __future__ import annotations

from app.harness.assembly import assembler
from app.services.featureflag import platform_settings
from app.services.featureflag.platform_settings import PLATFORM_FLAG_DEFAULTS


def test_followups_come_with_the_harness_and_no_labs_switch_is_read() -> None:
    # tests/conftest.py turns them off for the suite; the product's value is the module's own.
    assert "FOLLOWUP_SUGGESTIONS = True" in open(assembler.__file__, encoding="utf-8").read()
    assert not hasattr(platform_settings, "is_followup_suggestions_enabled")
    assert "ENABLE_FOLLOWUP_SUGGESTIONS" not in PLATFORM_FLAG_DEFAULTS
