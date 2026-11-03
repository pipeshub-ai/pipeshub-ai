import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Deadline:
    """A point in time a whole operation must finish by, shared by every step it takes."""

    expires_at: float
    clock: Callable[[], float] = time.monotonic

    @classmethod
    def after(cls, seconds: float, clock: Callable[[], float] = time.monotonic) -> "Deadline":
        return cls(clock() + seconds, clock)

    def remaining(self) -> float:
        return max(0.0, self.expires_at - self.clock())

    def cap(self, seconds: float) -> float:
        """``seconds``, shortened so it ends no later than the deadline."""
        return min(seconds, self.remaining())

    @property
    def expired(self) -> bool:
        return self.remaining() <= 0
