"""Canonical forms for emails, URLs, phones and IP addresses."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.values import ContactValue
from app.utils.url_redaction import SENSITIVE_QUERY_PARAMS

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Signing and tracking parameters change per copy of a link and say nothing about
# what it points at; credentials must never reach a key or the graph.
_VOLATILE_PARAM = re.compile(r"^(?:x-amz-|x-goog-|utm_)|^(?:fbclid|gclid|mc_cid|mc_eid)$")


def _query_key(query: str) -> str:
    """The query identifies the resource (``?id=``, ``?v=``): kept, sorted, without
    credentials or per-copy parameters."""
    kept = sorted(
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key.casefold() not in SENSITIVE_QUERY_PARAMS and not _VOLATILE_PARAM.match(key.casefold())
    )
    return urlencode(kept)


def normalize_contact(kind: EntityKind, surface: str) -> ContactValue | None:
    text = (surface or "").strip()
    if kind is EntityKind.EMAIL:
        if not _EMAIL.match(text):
            return None
        # The local part is case-sensitive by RFC 5321 but by no mainstream provider,
        # and "Jane.Doe@" and "jane.doe@" are one person to every reader.
        return ContactValue(scheme="email", canonical=text.casefold())
    if kind is EntityKind.URL:
        parsed = urlsplit(text if "://" in text else f"https://{text}")
        if not parsed.netloc:
            return None
        # userinfo (``user:pass@``) is dropped: it is a credential, not the resource.
        host = parsed.netloc.rsplit("@", 1)[-1].casefold()
        canonical = urlunsplit((
            (parsed.scheme or "https").casefold(),
            host,
            parsed.path.rstrip("/") or "",
            _query_key(parsed.query),
            "",
        ))
        return ContactValue(scheme="url", canonical=canonical)
    if kind is EntityKind.PHONE:
        digits = re.sub(r"\D", "", text)
        if not (10 <= len(digits) <= 15):
            return None
        try:
            import phonenumbers
            parsed = phonenumbers.parse(text, "US")
            if phonenumbers.is_possible_number(parsed):
                canonical = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
                return ContactValue(scheme="phone", canonical=canonical)
        except Exception:
            pass
        return ContactValue(scheme="phone", canonical=f"+{digits}")
    if kind is EntityKind.IP:
        try:
            canonical = str(ipaddress.ip_interface(text))
        except ValueError:
            return None
        return ContactValue(scheme="ip", canonical=canonical)
    return None
