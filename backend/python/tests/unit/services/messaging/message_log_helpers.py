"""Run a consumer's private parse-and-handle step the way its loop does: inside a delivery."""

import logging
from collections.abc import Awaitable
from typing import TypeVar

from app.services.messaging import message_log

T = TypeVar("T")


async def in_delivery(step: Awaitable[T]) -> T:
    """The bindings need an open delivery (they raise without one); its end line goes to a test logger."""
    with message_log.delivery(logging.getLogger("tests.message_log_helpers"), topic="t", message_id="m"):
        return await step
