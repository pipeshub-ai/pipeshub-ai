"""The short forms a record wrote a company or product under become aliases of its node."""

from __future__ import annotations

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import Mention, NamedEntity
from app.modules.named_entities.graph_writer import learned_aliases
from app.modules.named_entities.normalizers.names import norm_key_for

K = EntityKind


def _entity(kind: EntityKind, name: str, *surfaces: str) -> NamedEntity:
    return NamedEntity(
        kind=kind, display_name=name, norm_key=norm_key_for(kind, name, None),
        mentions=[
            Mention(block_index=i, char_start=0, char_end=len(s), surface=s, extractor="agent")
            for i, s in enumerate((name, *surfaces))
        ],
    )


def test_a_folded_short_form_is_learned_with_its_key() -> None:
    entity = _entity(K.ORGANIZATION, "Pinecrest Hospitality", "Pinecrest", "Pinecrest Hospitality", "Pinecrest")
    assert learned_aliases(entity) == (["Pinecrest"], [norm_key_for(K.ORGANIZATION, "Pinecrest", None)])


def test_a_spelling_with_the_same_key_is_not_an_alias() -> None:
    # Case and legal form normalize to the node's own key.
    assert learned_aliases(_entity(K.ORGANIZATION, "Pinecrest Hospitality", "PINECREST HOSPITALITY", "Pinecrest Hospitality Inc.")) == ([], [])


def test_a_persons_first_name_is_never_an_alias() -> None:
    # "Maya" is every Maya: an alias would tie them all to this one.
    assert learned_aliases(_entity(K.PERSON, "Maya Castellanos", "Maya")) == ([], [])


def test_a_two_letter_acronym_is_not_learned_but_a_longer_one_is() -> None:
    displays, _ = learned_aliases(_entity(K.ORGANIZATION, "Information Technology Services", "IT", "ITS"))
    assert displays == ["ITS"]


def test_a_product_short_form_is_learned() -> None:
    displays, _ = learned_aliases(_entity(K.PRODUCT, "Lumora Edge Pro", "Edge Pro"))
    assert displays == ["Edge Pro"]
