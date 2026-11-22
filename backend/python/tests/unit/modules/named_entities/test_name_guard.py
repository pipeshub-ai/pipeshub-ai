"""The name guard: what it refuses as a name of a semantic kind, and what it
must keep. The kept cases are real names that a naive rule would drop."""
import pytest

from app.modules.named_entities.domain.kinds import (
    KIND_GUIDE,
    EntityKind,
    EntitySource,
    spec_for,
)
from app.modules.named_entities.guardrails import name_rejection
from app.modules.named_entities.normalizers.dates import NormalizationContext
from app.modules.named_entities.strategies.agent import prompt
from app.modules.named_entities.strategies.agent.tools import (
    ExtractionSession,
    stage_wire_entity,
)
from app.modules.named_entities.text import TextUnit
from app.modules.named_entities.wire import WireEntity

K = EntityKind


@pytest.mark.parametrize(
    ("kind", "text", "reason"),
    [
        (K.PRODUCT, "purchase order", "description"),
        (K.PRODUCT, "internal dashboards", "description"),
        (K.EVENT, "site review", "description"),
        (K.ORGANIZATION, "the company", "description"),
        (K.ORGANIZATION, "procurement team", "description"),
        (K.LOCATION, "training room", "description"),
        (K.PERSON, "our account manager", "description"),
        (K.ORGANIZATION, "PA-2404", "identifier"),
        (K.ORGANIZATION, "LMS-MSA-2025-006", "identifier"),
        (K.PERSON, "INC-2025-001", "identifier"),
        (K.PRODUCT, "a130f2ea-6a53-4d11-8fd8-7e9acbf813f6", "identifier"),
        (K.LOCATION, "backend/python/app/connectors/core/constants.py", "identifier"),
        (K.PRODUCT, "integration-tests/connectors/gitlab", "identifier"),
        (K.PRODUCT, "issue_reconciler.py", "identifier"),
        (K.PRODUCT, "REPORT.md", "identifier"),
        (K.EVENT, "001.txt", "identifier"),
        (K.ORGANIZATION, "https://github.com/pipeshub-ai", "identifier"),
        (K.PERSON, "darshan.godase@pipeshub.com", "identifier"),
        (K.EVENT, "304", "no_letters"),
        (K.SKILL, "one two three four five six seven eight nine", "too_long"),
    ],
)
def test_what_is_never_a_name(kind: EntityKind, text: str, reason: str) -> None:
    assert name_rejection(kind, text) == reason


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (K.CITY, "The Hague"),
        (K.ORGANIZATION, "The Home Depot"),
        (K.ORGANIZATION, "The Economist"),
        (K.PRODUCT, "GPT-4"),
        (K.PRODUCT, "F-35"),
        (K.EVENT, "COVID-19"),
        (K.PRODUCT, "Edge Pro 2"),
        (K.PRODUCT, "ISO 27001"),
        (K.PRODUCT, "kubernetes"),
        (K.PRODUCT, "npm"),
        (K.PRODUCT, "iPhone"),
        (K.ORGANIZATION, "AT&T"),
        (K.ORGANIZATION, "McKinsey & Co."),
        (K.ORGANIZATION, "Acme (UK)"),
        (K.PERSON, "José Álvarez"),
        (K.PERSON, "田中 太郎"),
        (K.CITY, "القاهرة"),
        (K.ORGANIZATION, "टाटा मोटर्स"),
        (K.PERSON_TYPE, "account lead"),
        (K.SKILL, "contract negotiation"),
        (K.SKILL, "TCP/IP"),
        (K.ADDRESS, "4100 ridgeway drive"),
        (K.ADDRESS, "4100 Ridgeway Drive, Suite 200, Portland, OR 97201, United States of America"),
        (K.PRODUCT, "F-150"),
        (K.PRODUCT, "RTX-4090"),
        (K.EVENT, "COP-2025"),
        (K.PRODUCT, "PA-2404"),
        (K.CITY, "new york"),
        (K.COUNTRY_REGION, "united states"),
        (K.PRODUCT, "React/Node.js"),
        (K.SKILL, "Vue/Nuxt.js"),
        (K.PRODUCT, "Node.js"),
        (K.PRODUCT, "three.js"),
        (K.PRODUCT, "Socket.IO"),
        (K.PRODUCT, "ASP.NET"),
        (K.PRODUCT, "Microsoft.Graph.json"),
    ],
)
def test_real_names_are_kept(kind: EntityKind, text: str) -> None:
    assert name_rejection(kind, text) is None


def _session(text: str, *kinds: EntityKind) -> ExtractionSession:
    return ExtractionSession(
        [TextUnit(0, "b", text)], enabled=frozenset(kinds), norm_ctx=NormalizationContext(), seed_count=0,
    )


def test_a_rejected_name_is_dropped_and_counted_with_its_reason() -> None:
    session = _session("Please raise a purchase order with Acme Corp.", K.PRODUCT, K.ORGANIZATION)

    dropped = stage_wire_entity(session, 0, WireEntity(text="purchase order", block=0, kind="product", normalized=""))
    kept = stage_wire_entity(session, 1, WireEntity(text="Acme Corp", block=0, kind="organization", normalized=""))

    assert dropped["status"] == "dropped" and "do not resubmit" in dropped["error"]
    assert kept["status"] == "accepted"
    assert len(session.staging) == 1
    assert session.staging.rejected_reasons == {"not_a_name:description": 1}
    # Not a correctable rejection: it earns no feedback turn and keeps a clean finish.
    assert session.staging.rejected == 0


def test_pattern_kinds_are_not_judged_as_names() -> None:
    session = _session("Write to ops@acme.com or see https://acme.com/status.", K.EMAIL, K.URL)

    email = stage_wire_entity(session, 0, WireEntity(text="ops@acme.com", block=0, kind="email", normalized=""))
    url = stage_wire_entity(session, 1, WireEntity(text="https://acme.com/status", block=0, kind="url", normalized=""))

    assert email["status"] == "accepted" and url["status"] == "accepted"


def test_every_semantic_kind_has_a_guide_and_the_prompt_carries_it() -> None:
    semantic = {kind for kind in EntityKind if (spec := spec_for(kind)) and spec.source is EntitySource.SEMANTIC}
    assert semantic == set(KIND_GUIDE)
    for kind, text in KIND_GUIDE.items():
        assert f"- {kind.value}: {text}" in prompt.STATIC_PREFIX


def test_the_prompt_prefix_is_static() -> None:
    """Built once at import: the same bytes for every record keep provider caching."""
    assert "{kinds}" not in prompt.STATIC_PREFIX
    assert prompt.STATIC_PREFIX.startswith("You extract named entities")
