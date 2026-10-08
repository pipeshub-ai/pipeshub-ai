"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/saml."""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import uuid
import zlib
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import jwt
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from lxml import etree

from helper.http.api_client import APIClient
from helper.pipeshub_client import PipeshubClient

SAML_BASE = "/api/v1/saml"
SIGN_IN_ROUTE = f"{SAML_BASE}/signIn"
SIGN_IN_CALLBACK_ROUTE = f"{SAML_BASE}/signIn/callback"
DESKTOP_EXCHANGE_ROUTE = f"{SAML_BASE}/desktop/exchange"
UPDATE_APP_CONFIG_ROUTE = f"{SAML_BASE}/updateAppConfig"

FETCH_CONFIG_SCOPE = "fetch:config"
# A real scope updateAppConfig does not accept.
OTHER_SCOPE = "mail:send"
WRONG_SECRET = "spec-audit-not-the-deployment-secret"

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

# Well-formed (64 lowercase hex) but never issued, so redeem finds nothing in Redis.
MISSING_HANDOFF_CODE = "0123456789abcdef" * 4
MALFORMED_HANDOFF_CODE = "not-a-handoff-code"
# 43-128 chars of [A-Za-z0-9._~-], the PKCE verifier shape redeem requires.
CODE_VERIFIER = "spec-audit-verifier-" + "a" * 30
OTHER_CODE_VERIFIER = "spec-audit-verifier-" + "b" * 30
MALFORMED_CODE_VERIFIER = "too-short"
CODE_CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(CODE_VERIFIER.encode()).digest())
    .rstrip(b"=")
    .decode()
)
DESKTOP_STATE = "phd.specaudit0123456789"

LOGIN_PATH = "/login"
SUCCESS_PATH = "/auth/sign-in/samlSso/success"
# SAML_LOGOUT_UNSUPPORTED_MESSAGE in saml.controller.ts.
LOGOUT_UNSUPPORTED = (
    "Signing out through your identity provider isn't supported. "
    "To sign out, use Sign out in PipesHub."
)
UNKNOWN_STRATEGY = 'Unknown authentication strategy "saml"'

IDP_ENTRY_POINT = "http://127.0.0.1:9/spec-audit-dummy-idp/sso"
IDP_ISSUER = "spec-audit-dummy-idp"
# The orchestrator's cleanup script only deletes an SSO setting carrying this name.
IDP_PLATFORM = "spec-audit-dummy"
EMAIL_ATTRIBUTE = "email"

_SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
_SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
_DS = "http://www.w3.org/2000/09/xmldsig#"
_EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
_NS = {"saml": _SAML, "samlp": _SAMLP, "ds": _DS}


def scoped_jwt_secret() -> str:
    return os.getenv("SCOPED_JWT_SECRET", "").strip()


def mint_scoped_token(
    secret: str,
    scopes: list[str] | None,
    *,
    ttl_seconds: int = 3600,
    **claims: Any,
) -> str:
    """Service token shaped like Node's createJwt ones; a negative ttl gives an expired one."""
    now = datetime.datetime.now(datetime.timezone.utc)
    payload: dict[str, Any] = {
        **claims,
        "iat": now,
        "exp": now + datetime.timedelta(seconds=ttl_seconds),
    }
    if scopes is not None:
        payload["scopes"] = scopes
    return jwt.encode(payload, secret, algorithm="HS256")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def relay_state(**fields: Any) -> str:
    """Base64 JSON, the RelayState encoding the sign-in and callback routes read."""
    return base64.b64encode(json.dumps(fields).encode()).decode()


def desktop_relay_state(**fields: Any) -> str:
    return relay_state(client="desktop", state=DESKTOP_STATE, codeChallenge=CODE_CHALLENGE, **fields)


def redirect_target(resp: requests.Response) -> tuple[str, dict[str, list[str]]]:
    """Path and query of the redirect a SAML route answered with."""
    target = urlsplit(resp.headers.get("Location", ""))
    return target.path, parse_qs(target.query)


def _el(parent: Any, prefix: str, name: str, text: str | None = None, **attrs: str) -> Any:
    element = etree.SubElement(parent, f"{{{_NS[prefix]}}}{name}", attrib=attrs)
    element.text = text
    return element


def _instant(moment: datetime.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class DummyIdp:
    """A SAML identity provider that exists only as a key pair.

    Its certificate is what gets saved as the org's SSO setting; ``response`` builds the
    form field a browser would post back after signing in there, signed with the same key.
    """

    def __init__(self) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, IDP_ISSUER)])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self._key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=2))
            .sign(self._key, hashes.SHA256())
        )
        self.certificate_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()

    def setting(self, *, enable_jit: bool = False) -> dict[str, Any]:
        """A body for POST /configurationManager/authConfig/sso."""
        return {
            "entryPoint": IDP_ENTRY_POINT,
            "certificate": self.certificate_pem,
            "emailKey": EMAIL_ATTRIBUTE,
            "enableJit": enable_jit,
            "samlPlatform": IDP_PLATFORM,
        }

    def response(
        self,
        email: str,
        audience: str,
        *,
        signed: bool = True,
        valid_for_seconds: int = 300,
        email_attribute: str | None = EMAIL_ATTRIBUTE,
    ) -> str:
        """Base64 ``SAMLResponse`` asserting ``email`` to the service provider ``audience``.

        ``signed=False`` leaves the signature out and a negative ``valid_for_seconds`` gives
        an assertion that has already expired.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        not_before = _instant(now - datetime.timedelta(seconds=60))
        not_after = _instant(now + datetime.timedelta(seconds=valid_for_seconds))
        issued = _instant(now)

        response = etree.Element(
            f"{{{_SAMLP}}}Response",
            nsmap={"samlp": _SAMLP, "saml": _SAML},
            ID=f"_{uuid.uuid4().hex}",
            Version="2.0",
            IssueInstant=issued,
        )
        _el(response, "saml", "Issuer", IDP_ISSUER)
        status = _el(response, "samlp", "Status")
        _el(status, "samlp", "StatusCode", Value="urn:oasis:names:tc:SAML:2.0:status:Success")

        assertion_id = f"_{uuid.uuid4().hex}"
        assertion = _el(
            response, "saml", "Assertion", ID=assertion_id, Version="2.0", IssueInstant=issued
        )
        _el(assertion, "saml", "Issuer", IDP_ISSUER)
        subject = _el(assertion, "saml", "Subject")
        _el(
            subject,
            "saml",
            "NameID",
            f"spec-audit-subject-{uuid.uuid4().hex[:8]}",
            Format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
        )
        confirmation = _el(
            subject, "saml", "SubjectConfirmation", Method="urn:oasis:names:tc:SAML:2.0:cm:bearer"
        )
        _el(confirmation, "saml", "SubjectConfirmationData", NotOnOrAfter=not_after)
        conditions = _el(
            assertion, "saml", "Conditions", NotBefore=not_before, NotOnOrAfter=not_after
        )
        _el(_el(conditions, "saml", "AudienceRestriction"), "saml", "Audience", audience)
        statement = _el(
            assertion, "saml", "AuthnStatement", AuthnInstant=issued, SessionIndex=assertion_id
        )
        _el(
            _el(statement, "saml", "AuthnContext"),
            "saml",
            "AuthnContextClassRef",
            "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport",
        )
        if email_attribute is not None:
            attributes = _el(assertion, "saml", "AttributeStatement")
            _el(
                _el(attributes, "saml", "Attribute", Name=email_attribute),
                "saml",
                "AttributeValue",
                email,
            )

        if signed:
            self._sign(assertion, assertion_id)
        return base64.b64encode(etree.tostring(response)).decode()

    def _sign(self, assertion: Any, assertion_id: str) -> None:
        """Enveloped RSA-SHA256 signature over the assertion, placed after its Issuer."""
        digest = hashlib.sha256(etree.tostring(assertion, method="c14n", exclusive=True)).digest()

        signature = etree.Element(f"{{{_DS}}}Signature", nsmap={"ds": _DS})
        signed_info = _el(signature, "ds", "SignedInfo")
        _el(signed_info, "ds", "CanonicalizationMethod", Algorithm=_EXC_C14N)
        _el(
            signed_info,
            "ds",
            "SignatureMethod",
            Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
        )
        reference = _el(signed_info, "ds", "Reference", URI=f"#{assertion_id}")
        transforms = _el(reference, "ds", "Transforms")
        _el(transforms, "ds", "Transform", Algorithm=f"{_DS}enveloped-signature")
        _el(transforms, "ds", "Transform", Algorithm=_EXC_C14N)
        _el(reference, "ds", "DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
        _el(reference, "ds", "DigestValue", base64.b64encode(digest).decode())
        value = _el(signature, "ds", "SignatureValue")

        assertion.insert(1, signature)
        signed_bytes = etree.tostring(signed_info, method="c14n", exclusive=True)
        value.text = base64.b64encode(
            self._key.sign(signed_bytes, padding.PKCS1v15(), hashes.SHA256())
        ).decode()


def authn_request_issuer(location: str) -> str:
    """The service provider's entity id, read from the AuthnRequest the sign-in redirect carries."""
    encoded = parse_qs(urlsplit(location).query).get("SAMLRequest", [""])[0]
    assert encoded, f"the IdP redirect carries no SAMLRequest: {location[:300]}"
    document = etree.fromstring(zlib.decompress(base64.b64decode(encoded), -15))
    issuer = document.find(f"{{{_SAML}}}Issuer")
    assert issuer is not None and issuer.text, "the AuthnRequest has no Issuer"
    return issuer.text


class SamlClient(APIClient):
    """Client for /api/v1/saml.

    No route takes a session token: three are public and updateAppConfig takes
    a scoped service token (``token=``). The browser-facing routes answer with
    redirects, which are returned as-is so the 302 itself can be checked.
    """

    BASE = SAML_BASE

    def sign_in(self, *, auth: bool = False, **params: Any) -> requests.Response:
        return self.get("/signIn", auth=auth, params=params, allow_redirects=False)

    def sign_in_callback(
        self,
        form: dict[str, Any] | None = None,
        *,
        auth: bool = False,
        **kwargs: Any,
    ) -> requests.Response:
        """POST the IdP's form-encoded body (SAMLResponse, RelayState, ...)."""
        if form is not None:
            kwargs.setdefault("data", form)
        kwargs.setdefault("allow_redirects", False)
        return self.post("/signIn/callback", auth=auth, **kwargs)

    def desktop_exchange(
        self, body: Any = None, *, auth: bool = False, **kwargs: Any
    ) -> requests.Response:
        if body is not None:
            kwargs.setdefault("json", body)
        return self.post("/desktop/exchange", auth=auth, **kwargs)

    def update_app_config(
        self, *, token: str | None = None, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        """``token`` sends a scoped token; otherwise ``auth`` picks admin session token or none."""
        if token is not None:
            # auth=False so the helper neither overwrites the header nor
            # retries a 401 with a refreshed admin token.
            return self.post(
                "/updateAppConfig", auth=False, headers=bearer(token), **kwargs
            )
        return self.post("/updateAppConfig", auth=auth, **kwargs)


# Base64 of a string that is not XML, so no IdP's key could have signed it.
NOT_A_SAML_RESPONSE = "c3BlYy1hdWRpdC1ub3QtYS1zYW1sLXJlc3BvbnNl"


def saml_strategy_registered(client: SamlClient) -> bool:
    """Whether this Node process has a SAML IdP registered (passport "saml" strategy).

    Read from the public callback: with nothing registered, passport's error is
    the saml_error of the redirect; nothing is changed either way.
    """
    resp = client.sign_in_callback({"SAMLResponse": NOT_A_SAML_RESPONSE})
    assert resp.status_code == 302, resp.text[:500]
    return UNKNOWN_STRATEGY not in unquote(resp.headers.get("Location", ""))


def register_idp(client: PipeshubClient, idp: DummyIdp, *, enable_jit: bool = False) -> None:
    """Save ``idp`` as the org's SSO setting, which also registers it with passport."""
    resp = client.request(
        "POST", "/api/v1/configurationManager/authConfig/sso", json=idp.setting(enable_jit=enable_jit)
    )
    assert resp.status_code == 200, f"saving the SSO settings failed: {resp.status_code} {resp.text[:300]}"
