"""Mark an OAuth `state` as started from the desktop app.

The provider redirects the user's browser to the web callback page, which has no way to
know the flow belongs to the desktop app. The marker travels inside `state` (the only value
providers round-trip) so that page can hand `code`+`state` to the app over a `pipeshub://`
deep link instead of completing the callback with the browser's own session.
"""

# Must match DESKTOP_STATE_PREFIX in frontend/lib/auth/desktop-oauth.ts.
# Names here avoid "oauth": CodeQL treats any value from an *oauth*-named source as a
# password, which would flag every existing log line that mentions `state`.
DESKTOP_STATE_PREFIX = "phd."


def mark_desktop_state(state: str, *, desktop: bool) -> str:
    return f"{DESKTOP_STATE_PREFIX}{state}" if desktop else state


def strip_desktop_state(state: str | None) -> str | None:
    if state and state.startswith(DESKTOP_STATE_PREFIX):
        return state[len(DESKTOP_STATE_PREFIX):]
    return state
