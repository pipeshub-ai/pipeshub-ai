"""Typed refusals of knowledge publications, each with a stable ``reason_code`` (C-6: unique)."""

from __future__ import annotations

from harness_core.refusals import HarnessRefusal


class PublicationRefused(HarnessRefusal):
    reason_code: str = "publication_refused"
    http_status: int = 403


class PublicationAnswerOnly(PublicationRefused):
    """The record reached the caller through an ``answer_only`` publication: answers yes, raw reads no."""

    reason_code = "publication_answer_only"


class PublicationForbidden(PublicationRefused):
    """Only an owner of the container or an org admin may publish it, list or revoke its publications."""

    reason_code = "publication_forbidden"


class PublicationInvalid(PublicationRefused):
    reason_code = "publication_invalid"
    http_status = 422


class PublicationNotFound(PublicationRefused):
    """No such publication or container in the caller's org."""

    reason_code = "publication_not_found"
    http_status = 404


class PublicationsDisabled(PublicationRefused):
    """``ENABLE_KNOWLEDGE_PUBLICATIONS`` is off for the org."""

    reason_code = "publications_disabled"
    http_status = 404


__all__ = [
    "PublicationAnswerOnly",
    "PublicationForbidden",
    "PublicationInvalid",
    "PublicationNotFound",
    "PublicationRefused",
    "PublicationsDisabled",
]
