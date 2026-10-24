from app.utils.desktop_state import (
    DESKTOP_STATE_PREFIX,
    mark_desktop_state,
    strip_desktop_state,
)


class TestDesktopOauthState:
    def test_web_state_is_untouched(self) -> None:
        assert mark_desktop_state("abc", desktop=False) == "abc"

    def test_desktop_state_round_trips(self) -> None:
        marked = mark_desktop_state("eyJzdGF0ZSI6ICJ4In0=", desktop=True)
        assert marked == f"{DESKTOP_STATE_PREFIX}eyJzdGF0ZSI6ICJ4In0="
        assert strip_desktop_state(marked) == "eyJzdGF0ZSI6ICJ4In0="

    def test_strip_leaves_unmarked_and_missing_state_alone(self) -> None:
        assert strip_desktop_state("abc") == "abc"
        assert strip_desktop_state(None) is None
        assert strip_desktop_state("") == ""
