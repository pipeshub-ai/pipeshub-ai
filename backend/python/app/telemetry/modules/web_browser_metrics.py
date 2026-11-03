"""Metrics for the web connector's shared headless browser.

One browser serves every web connector in the process, so nothing here is
labelled by connector or org.
"""

from app.telemetry.backend import METRICS_BACKEND

RESTARTS = METRICS_BACKEND.counter(
    "pipeshub_web_browser_restarts_total",
    "Relaunches of the shared headless browser, by why it was relaunched and how that went",
    ["reason", "outcome"],
)

REJECTED_FETCHES = METRICS_BACKEND.counter(
    "pipeshub_web_browser_rejected_fetches_total",
    "Fetches refused without touching the browser because it could not be relaunched",
    ["reason"],
)


def record_restart(reason: str, *, ok: bool) -> None:
    RESTARTS.inc(reason, "ok" if ok else "failed")


def record_rejected_fetch(reason: str) -> None:
    REJECTED_FETCHES.inc(reason)
