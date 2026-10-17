"""A node key must never change silently.

The rows below are the keys this recipe produced when it was frozen. A failure
here means node identity moved: stored nodes would stop matching new writes.
Fix the code, or ship the change as a rekey migration (see the docs) and
re-pin deliberately with a new KEY_SCHEME.
"""

import re

import pytest

from app.modules.named_entities.domain.kinds import EntityKind as K
from app.modules.named_entities.keys import KEY_SCHEME, named_entity_key
from app.modules.named_entities.normalizers import normalize_value
from app.modules.named_entities.normalizers.dates import NormalizationContext
from app.modules.named_entities.normalizers.names import norm_key_for

CTX = NormalizationContext(reference_time_ms=1767225600000, tz="UTC")
ORG = "org-1"

GOLDEN = [
    (K.ORGANIZATION, "Acme Inc.", "name:organization:acme", "19c3367e-8bcd-587b-a40f-b060aa73a0a7"),
    (K.ORGANIZATION, "ACME, Inc", "name:organization:acme", "19c3367e-8bcd-587b-a40f-b060aa73a0a7"),
    (K.ORGANIZATION, "Müller & Söhne AG", "name:organization:müller & söhne", "4a0eae20-64bd-57d2-9d02-3b79d9f56709"),
    (K.ORGANIZATION, "Zoë Café Ltd.", "name:organization:zoë café", "a1915ed7-8721-5dc5-ba8c-3b35a9565eb7"),
    (K.PERSON, "jane o'neil", "name:person:jane o'neil", "bef54830-4a0c-52e8-903b-16674676e94c"),
    (K.PRODUCT, "Apple", "name:product:apple", "33474579-e8ca-552f-87b7-af8f5e5672cb"),
    (K.ORGANIZATION, "Apple", "name:organization:apple", "29643342-7068-572a-b388-4d965e0bba6c"),
    (K.LOCATION, "São Paulo", "name:location:são paulo", "9d2b8d32-3c33-5974-a934-117714c77ec4"),
    (K.LOCATION, "San Francisco, CA", "name:location:san francisco, ca", "e2cae33f-4009-5b81-9328-033c8c586cae"),
    (K.CURRENCY, "$1,250", "money:USD:1250", "3cb69e03-6cb5-5447-bff4-b5e3a62448f4"),
    (K.CURRENCY, "USD 1,250.00", "money:USD:1250", "3cb69e03-6cb5-5447-bff4-b5e3a62448f4"),
    (K.CURRENCY, "€99.50", "money:EUR:99.5", "5927f5ea-207f-5473-8873-e9a368d83c2d"),
    (K.PERCENTAGE, "5%", "pct:0.05", "c1dc1c49-acd1-5405-9e5b-a66b116062a6"),
    (K.PERCENTAGE, "300 bps", "pct:0.03", "6aaade69-98c3-5d3d-a25e-ddb0286527e5"),
    (K.DIMENSION, "5 km", "qty:length:5000", "49ca66bb-aaab-58e7-9e23-7e176394ce09"),
    (K.DIMENSION, "5000 m", "qty:length:5000", "49ca66bb-aaab-58e7-9e23-7e176394ce09"),
    (K.DIMENSION, "1 mi", "qty:length:1609.344", "907e73f8-993b-5eff-9286-8e3db0550e33"),
    (K.DIMENSION, "1609.344 m", "qty:length:1609.344", "907e73f8-993b-5eff-9286-8e3db0550e33"),
    (K.DIMENSION, "72 F", "qty:temperature:295.372222222", "d8ad7a6e-5b3d-5a64-bc49-89188b0f3354"),
    (K.DIMENSION, "22.2222222222222 C", "qty:temperature:295.372222222", "d8ad7a6e-5b3d-5a64-bc49-89188b0f3354"),
    (K.DIMENSION, "3 kg", "qty:weight:3", "0a7d06fc-4066-5f6c-828d-2bb0cb41190e"),
    (K.DATE, "15 March 2026", "date:day:1773532800000:1773619200000", "0a7513a9-9e5e-5390-8f62-e8c803dce582"),
    (K.DATE_RANGE, "Q3 2026", "date:quarter:1782864000000:1790812800000", "5d748443-c549-579c-b3fd-1214f163621d"),
    (K.DATE_RANGE, "FY2025", "date:year:1735689600000:1767225600000", "5b4de3b5-0cf4-56a2-a983-ac3b9fcd3155"),
    (K.DATE_RANGE, "March 2026", "date:month:1772323200000:1775001600000", "7ffbbd5e-31a7-54df-8d7f-8e696d522bb6"),
    (K.DATE_TIME, "2026-03-15T14:30:00Z", "date:minute:1773585000000:1773585060000", "bf017c7a-84c8-5ffb-8501-8ae7a71081bc"),
    (K.DATE, "12 April", "date:unresolved:XXXX-04-12", "7696903c-c1d1-500e-af9a-95b36610bad1"),
    (K.DURATION, "3 months", "duration:P3M", "b769b730-5562-5ec3-b0ab-94ba21a5187c"),
    (K.EMAIL, "Jane.Doe@Example.com", "email:jane.doe@example.com", "e40ff1c8-8b38-5a36-bd37-3c6582f8ec95"),
    (K.URL, "https://Example.com/a?b=1", "url:https://example.com/a?b=1", "7a71df64-5c5e-5df4-9396-1f09a6bde043"),
]


def _norm_key(kind: K, surface: str, ctx: NormalizationContext = CTX) -> str:
    return norm_key_for(kind, surface, normalize_value(kind, surface, ctx))


def _key(kind: K, surface: str, org: str = ORG, ctx: NormalizationContext = CTX) -> str:
    return named_entity_key(org, kind.value, _norm_key(kind, surface, ctx))


@pytest.mark.parametrize(("kind", "surface", "norm_key", "key"), GOLDEN, ids=[f"{r[0].name}:{r[1]}" for r in GOLDEN])
def test_keys_are_frozen(kind, surface, norm_key, key):
    assert _norm_key(kind, surface) == norm_key
    assert _key(kind, surface) == key


def test_the_recipe_version_is_one():
    # Bumping it means the corpus above was re-pinned on purpose.
    assert KEY_SCHEME == 1


@pytest.mark.parametrize(
    ("kind", "left", "right"),
    [
        (K.ORGANIZATION, "Acme Inc.", "ACME, Inc"),
        (K.DIMENSION, "5 km", "5000 m"),
        (K.DIMENSION, "1 mi", "1609.344 m"),
        (K.DIMENSION, "72 F", "22.2222222222222 C"),
        (K.CURRENCY, "$1,250", "USD 1,250.00"),
        (K.ORGANIZATION, "Bain & Co", "Bain"),
        (K.ORGANIZATION, "Johnson and Co.", "Johnson"),
        (K.ORGANIZATION, "Siemens AG", "Siemens"),
        (K.ORGANIZATION, "Infosys Pvt Ltd", "Infosys Private Limited"),
        (K.ORGANIZATION, "Porsche GmbH & Co. KG", "Porsche"),
        (K.ORGANIZATION, "Acme Co Ltd", "Acme"),
        (K.EMAIL, "Jane.Doe@Example.com", "jane.doe@example.com"),
        (K.PERSON, "Jane O\u2019Neil", "jane o'neil"),
        (K.URL, "https://example.com/a?b=1&c=2", "https://EXAMPLE.com/a/?c=2&b=1#top"),
        (K.URL, "https://example.com/a?b=1", "https://example.com/a?b=1&utm_source=mail&gclid=x"),
    ],
)
def test_spellings_of_one_thing_share_a_key(kind, left, right):
    assert _key(kind, left) == _key(kind, right)


@pytest.mark.parametrize(
    ("kind", "left", "right"),
    [
        (K.ORGANIZATION, "Acme Robotics", "Acme Foods"),
        (K.CURRENCY, "$5", "€5"),
        (K.DIMENSION, "5 km", "5 mi"),
        (K.DATE_RANGE, "Q3 2026", "Q4 2026"),
        (K.URL, "https://youtube.com/watch?v=abc", "https://youtube.com/watch?v=xyz"),
        (K.URL, "https://example.com/doc?id=1", "https://example.com/doc?id=2"),
    ],
)
def test_different_things_do_not_share_a_key(kind, left, right):
    assert _key(kind, left) != _key(kind, right)


def test_a_percentage_and_an_amount_of_five_are_different_nodes():
    assert _key(K.CURRENCY, "$5") != _key(K.PERCENTAGE, "5%")


def test_same_name_never_shares_a_node_across_kinds_or_orgs():
    assert _key(K.PRODUCT, "Apple") != _key(K.ORGANIZATION, "Apple")
    assert _key(K.ORGANIZATION, "Apple", org="org-1") != _key(K.ORGANIZATION, "Apple", org="org-2")


def test_a_timezone_change_mints_a_new_date_node_rather_than_rewriting_one():
    berlin = NormalizationContext(reference_time_ms=CTX.reference_time_ms, tz="Europe/Berlin")
    assert _key(K.DATE, "15 March 2026", ctx=berlin) != _key(K.DATE, "15 March 2026")


def test_built_in_kinds_stay_out_of_the_reserved_custom_namespace():
    from app.modules.named_entities.domain.kinds import EntityKind

    assert all(re.fullmatch(r"[a-z_]+", kind.value) for kind in EntityKind)


@pytest.mark.parametrize("kind", ["Organization", "onto:Vendor", "vendor.class", "x.acme", ""])
def test_a_key_refuses_anything_but_a_base_kind(kind):
    with pytest.raises(ValueError):
        named_entity_key("org", kind, "acme")


def test_an_org_defined_base_kind_has_its_own_namespace():
    assert named_entity_key("org", "x.acme.contract_id", "c-1") != named_entity_key("org", "contract_id", "c-1")


def test_an_article_alone_is_not_a_legal_suffix_left_over():
    assert _norm_key(K.ORGANIZATION, "The Limited") == "name:organization:the limited"


@pytest.mark.parametrize(
    "surface",
    [
        "https://user:hunter2@example.com/a?b=1",
        "https://example.com/a?b=1&token=secret",
        "https://example.com/a?b=1&X-Amz-Signature=abc&X-Amz-Credential=k",
        "https://example.com/a?b=1&access_token=t#frag",
    ],
)
def test_a_credential_in_a_link_never_reaches_its_key(surface):
    assert _norm_key(K.URL, surface) == "url:https://example.com/a?b=1"


def test_an_organization_shows_the_name_its_key_is_made_of():
    from app.modules.named_entities.normalizers.names import canonical_name

    for surface in ("Acme Co Ltd", "Porsche GmbH & Co. KG", "Fiat S.p.A."):
        display = canonical_name(surface, organization=True)
        assert canonical_name(display, organization=True) == display
        assert _norm_key(K.ORGANIZATION, surface) == f"name:organization:{display.casefold()}"


@pytest.mark.parametrize("name", ["AG", "The Limited", "Visa", "Nasa"])
def test_a_name_that_is_only_or_not_a_legal_form_stays_whole(name):
    assert _norm_key(K.ORGANIZATION, name) == f"name:organization:{name.casefold()}"

