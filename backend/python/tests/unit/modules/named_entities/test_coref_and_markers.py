"""One record's short forms join its full names, and mention markers are not
part of a name. The kept-apart cases are what a looser rule would merge wrongly."""
import pytest

from app.modules.named_entities.coref import link_short_forms
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import Mention, NamedEntity
from app.modules.named_entities.graph_writer import _embeddable
from app.modules.named_entities.normalizers.names import canonical_name, norm_key_for
from app.modules.named_entities.resolution import ResolvedEntity

K = EntityKind


def _entity(kind: EntityKind, name: str, block: int = 0) -> NamedEntity:
    return NamedEntity(
        kind=kind,
        display_name=name,
        norm_key=f"name:{kind.value}:{name.casefold()}",
        mentions=[Mention(block_index=block, block_id=f"b{block}", char_start=0, char_end=len(name), surface=name, extractor="agent")],
    )


def _names(entities: list[NamedEntity]) -> dict[str, list[str]]:
    return {e.display_name: [m.surface for m in e.mentions] for e in entities}


@pytest.mark.parametrize(
    ("surface", "kind", "name"),
    [
        ("@Darshan Godase", K.PERSON, "Darshan Godase"),
        ("Darshan Godase (You)", K.PERSON, "Darshan Godase"),
        ("Priya Shah (she/her)", K.PERSON, "Priya Shah"),
        ("@@Acme Corp", K.ORGANIZATION, "Acme"),
        ("Acme (UK)", K.ORGANIZATION, "Acme (UK)"),
        ("@angular/core", K.PRODUCT, "@angular/core"),
        ("(You)", K.PERSON, "(You)"),
    ],
)
def test_mention_markers_are_not_part_of_a_name(surface: str, kind: EntityKind, name: str) -> None:
    assert canonical_name(surface, kind) == name
    assert canonical_name(name, kind) == name


def test_a_marked_mention_keys_like_the_plain_name() -> None:
    plain = norm_key_for(K.PERSON, canonical_name("Darshan Godase", K.PERSON), None)
    for marked in ("@Darshan Godase", "Darshan Godase (You)", "@Darshan Godase (Guest)"):
        assert norm_key_for(K.PERSON, canonical_name(marked, K.PERSON), None) == plain


def test_a_first_name_joins_the_one_full_name_in_the_record() -> None:
    entities = [_entity(K.PERSON, "Maya Castellanos"), _entity(K.PERSON, "Maya", 3), _entity(K.PERSON, "Castellanos", 4)]
    assert _names(link_short_forms(entities)) == {"Maya Castellanos": ["Maya Castellanos", "Maya", "Castellanos"]}


def test_a_first_name_two_people_share_stays_apart() -> None:
    entities = [_entity(K.PERSON, "Lucas Adeyemi"), _entity(K.PERSON, "Lucas Nakamura"), _entity(K.PERSON, "Lucas")]
    assert set(_names(link_short_forms(entities))) == {"Lucas Adeyemi", "Lucas Nakamura", "Lucas"}


def test_an_initial_joins_first_so_the_first_name_has_one_full_form_left() -> None:
    entities = [_entity(K.PERSON, "Darshan Godase"), _entity(K.PERSON, "Darshan G"), _entity(K.PERSON, "Darshan")]
    assert _names(link_short_forms(entities)) == {"Darshan Godase": ["Darshan Godase", "Darshan G", "Darshan"]}


def test_an_initial_that_fits_two_people_stays_apart() -> None:
    entities = [_entity(K.PERSON, "Ana Garcia"), _entity(K.PERSON, "Ana Gomez"), _entity(K.PERSON, "Ana G.")]
    assert set(_names(link_short_forms(entities))) == {"Ana Garcia", "Ana Gomez", "Ana G."}


def test_an_organization_short_form_joins_through_a_designator_only() -> None:
    entities = [_entity(K.ORGANIZATION, "Lumora Systems"), _entity(K.ORGANIZATION, "Lumora")]
    assert _names(link_short_forms(entities)) == {"Lumora Systems": ["Lumora Systems", "Lumora"]}


@pytest.mark.parametrize(("parent", "child"), [("Google", "Google Cloud"), ("Amazon", "Amazon Web Services")])
def test_a_parent_and_its_division_stay_apart(parent: str, child: str) -> None:
    entities = [_entity(K.ORGANIZATION, child), _entity(K.ORGANIZATION, parent)]
    assert set(_names(link_short_forms(entities))) == {child, parent}


def test_an_acronym_joins_the_one_organization_it_abbreviates() -> None:
    entities = [_entity(K.ORGANIZATION, "Northwind Logistics"), _entity(K.ORGANIZATION, "NL")]
    assert _names(link_short_forms(entities)) == {"Northwind Logistics": ["Northwind Logistics", "NL"]}


def test_a_short_form_two_rules_point_two_ways_stays_apart() -> None:
    entities = [_entity(K.ORGANIZATION, "NL Systems"), _entity(K.ORGANIZATION, "Northwind Logistics"), _entity(K.ORGANIZATION, "NL")]
    assert set(_names(link_short_forms(entities))) == {"NL Systems", "Northwind Logistics", "NL"}


def test_kinds_never_join_each_other() -> None:
    entities = [_entity(K.PERSON, "Jordan Lee"), _entity(K.COUNTRY_REGION, "Jordan")]
    assert set(_names(link_short_forms(entities))) == {"Jordan Lee", "Jordan"}


def test_a_merged_mention_does_not_rename_its_targets_vector() -> None:
    entity = _entity(K.ORGANIZATION, "Northwind")
    merged = ResolvedEntity(entity=entity, graph_key="node-northwind-logistics", merged_into="node-northwind-logistics")
    own = ResolvedEntity(entity=_entity(K.ORGANIZATION, "Globex"), graph_key="node-globex")
    assert [r.entity_id for r in _embeddable("org", [merged, own])] == ["node-globex"]


@pytest.mark.parametrize(
    ("surface", "kind", "name"),
    [
        ("Darshan (You) (Guest)", K.PERSON, "Darshan"),
        ("Foo (Bot) LLC", K.ORGANIZATION, "Foo"),
        ("Priya Shah (you]", K.PERSON, "Priya Shah (you]"),
    ],
)
def test_stacked_markers_strip_to_a_fixed_point(surface: str, kind: EntityKind, name: str) -> None:
    assert canonical_name(surface, kind) == name
    assert canonical_name(name, kind) == name


def test_an_organization_chain_folds_whatever_the_input_order() -> None:
    for order in (("Lumora", "Lumora Systems", "Lumora Systems Group"), ("Lumora Systems Group", "Lumora", "Lumora Systems")):
        entities = [_entity(K.ORGANIZATION, name) for name in order]
        assert set(_names(link_short_forms(entities))) == {"Lumora Systems Group"}


@pytest.mark.parametrize(("acronym", "full"), [("P&G", "Procter & Gamble"), ("J&J", "Johnson & Johnson")])
def test_an_acronym_with_an_ampersand_joins_its_organization(acronym: str, full: str) -> None:
    entities = [_entity(K.ORGANIZATION, full), _entity(K.ORGANIZATION, acronym)]
    assert set(_names(link_short_forms(entities))) == {full}


def test_three_token_names_fold_their_last_name_and_initial() -> None:
    entities = [_entity(K.PERSON, "Mary Ann Smith"), _entity(K.PERSON, "Smith", 2), _entity(K.PERSON, "Mary S.", 5)]
    assert _names(link_short_forms(entities)) == {"Mary Ann Smith": ["Mary Ann Smith", "Smith", "Mary S."]}


def test_a_short_form_alone_in_its_record_is_kept() -> None:
    assert set(_names(link_short_forms([_entity(K.PERSON, "Maya")]))) == {"Maya"}


def test_folded_mentions_are_kept_in_document_order() -> None:
    entities = [_entity(K.PERSON, "Maya Castellanos", 4), _entity(K.PERSON, "Maya", 1)]
    [maya] = link_short_forms(entities)
    assert [m.block_index for m in maya.mentions] == [1, 4]


def test_an_entity_that_is_not_merged_is_embedded() -> None:
    survivor = ResolvedEntity(entity=_entity(K.ORGANIZATION, "Northwind Logistics"), graph_key="node-nwl")
    assert [(r.entity_id, r.name) for r in _embeddable("org", [survivor])] == [("node-nwl", "Northwind Logistics")]
