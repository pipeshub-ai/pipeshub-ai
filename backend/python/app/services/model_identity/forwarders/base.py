"""The contract between identity and a gateway's wire format."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from app.services.model_identity.projector import ForwardedIdentity
    from app.services.model_identity.settings import ConnectionForwardingSettings


@dataclass(frozen=True, slots=True)
class HeaderSet:
    """Headers to set on a request, and headers to drop from it.

    ``remove`` exists so a forwarder can replace a credential the SDK already
    attached (the ``Authorization`` header) and not send two.
    """

    set: tuple[tuple[str, str], ...] = ()
    remove: tuple[str, ...] = field(default=())
    # ``time.monotonic()`` after which these headers must be rendered again, for
    # forwarders whose output expires (signed tokens). None means they never do.
    expires_at: float | None = None

    def apply(self, headers: MutableMapping[str, str]) -> None:
        for name in self.remove:
            if name in headers:
                del headers[name]
        for name, value in self.set:
            headers[name] = value

    def as_dict(self) -> dict:
        return dict(self.set)


EMPTY_HEADERS = HeaderSet()


@runtime_checkable
class IdentityForwarder(Protocol):
    """Renders a ``ForwardedIdentity`` as headers.

    Pure and synchronous: it runs on the request path, so anything it needs from
    I/O (groups, signing keys) is prepared before it is called.
    """

    def requires_groups(self, settings: ConnectionForwardingSettings) -> bool:
        """Whether the output depends on group membership, so a lookup is worth waiting for."""
        ...

    def render(
        self, identity: ForwardedIdentity, settings: ConnectionForwardingSettings
    ) -> HeaderSet: ...
