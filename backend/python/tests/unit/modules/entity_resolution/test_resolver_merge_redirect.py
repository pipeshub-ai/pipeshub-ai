"""A name whose deterministic key is a merged-away node resolves to the node
it was merged into (KG-33 follow-up). Without this, tier 0 no longer finds
the loser, the name looks new, and its key is the loser's own key, so the
record would quietly link to the hidden node again, whatever the winner's
alias cap."""
from __future__ import annotations

from app.config.constants.arangodb import CollectionNames
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.entity_resolution.normalizer import normalize_name

LANGUAGES = CollectionNames.LANGUAGES.value
ORG = "acme"


def _node(fake_graph, key: str, name: str, *, org: str = ORG, aliases: tuple[str, ...] = (),
          merged_into: str | None = None) -> None:
    node = {"name": name, "normalizedName": normalize_name(name), "orgId": org, "aliases": list(aliases)}
    if merged_into:
        node["mergedInto"] = merged_into
    fake_graph.nodes[(LANGUAGES, key)] = node


def _english_key() -> str:
    return taxonomy_node_key(ORG, LANGUAGES, "english")


async def test_new_name_on_a_merged_key_resolves_to_the_winner(
    make_resolver, fake_graph, metadata_factory, ctx_factory,
) -> None:
    _node(fake_graph, _english_key(), "English", merged_into="win")
    _node(fake_graph, "win", "English (US)")
    meta = metadata_factory(languages=["english"])
    resolution = await make_resolver().resolve(ctx_factory("r1", ORG, meta))
    (entity,) = resolution.entries.values()
    assert (entity.key, entity.is_new, entity.decision) == ("win", False, "redirect")
    assert entity.name == "English (US)" and meta.languages == ["English (US)"]
    assert "English" in entity.new_aliases
    assert resolution.stats.new_nodes == 0


async def test_redirect_holds_when_the_winner_is_at_its_alias_cap(
    make_resolver, fake_graph, metadata_factory, ctx_factory,
) -> None:
    _node(fake_graph, _english_key(), "English", merged_into="win")
    _node(fake_graph, "win", "English (US)", aliases=tuple(f"a{i}" for i in range(20)))
    meta = metadata_factory(languages=["english"])
    resolution = await make_resolver().resolve(ctx_factory("r1", ORG, meta))
    (entity,) = resolution.entries.values()
    assert entity.key == "win" and entity.new_aliases == []


async def test_a_chain_of_merges_is_followed_to_its_end(
    make_resolver, fake_graph, metadata_factory, ctx_factory,
) -> None:
    _node(fake_graph, _english_key(), "English", merged_into="mid")
    _node(fake_graph, "mid", "Englisch", merged_into="end")
    _node(fake_graph, "end", "English language")
    resolution = await make_resolver().resolve(ctx_factory("r1", ORG, metadata_factory(languages=["english"])))
    (entity,) = resolution.entries.values()
    assert entity.key == "end"


async def test_a_redirect_out_of_the_org_is_not_followed(
    make_resolver, fake_graph, metadata_factory, ctx_factory,
) -> None:
    _node(fake_graph, _english_key(), "English", merged_into="theirs")
    _node(fake_graph, "theirs", "English", org="other")
    resolution = await make_resolver().resolve(ctx_factory("r1", ORG, metadata_factory(languages=["english"])))
    (entity,) = resolution.entries.values()
    assert entity.key == _english_key() and entity.is_new


async def test_lookup_failure_keeps_the_new_node(
    make_resolver, fake_graph, metadata_factory, ctx_factory,
) -> None:
    fake_graph.fail_node_lookup = True
    resolution = await make_resolver().resolve(ctx_factory("r1", ORG, metadata_factory(languages=["english"])))
    (entity,) = resolution.entries.values()
    assert entity.is_new and entity.key == _english_key()


async def test_two_names_redirecting_to_one_winner_become_one_entity(
    make_resolver, fake_graph, metadata_factory, ctx_factory,
) -> None:
    _node(fake_graph, _english_key(), "English", merged_into="win")
    french = taxonomy_node_key(ORG, LANGUAGES, "french")
    _node(fake_graph, french, "French", merged_into="win")
    _node(fake_graph, "win", "Bilingual")
    meta = metadata_factory(languages=["english", "french"])
    resolution = await make_resolver().resolve(ctx_factory("r1", ORG, meta))
    (entity,) = resolution.entries.values()
    assert entity.key == "win" and sorted(entity.new_aliases) == ["English", "French"]
    assert meta.languages == ["Bilingual"]


async def test_unmerged_new_names_cost_one_lookup_and_stay_new(
    make_resolver, fake_graph, metadata_factory, ctx_factory,
) -> None:
    resolution = await make_resolver().resolve(ctx_factory("r1", ORG, metadata_factory(languages=["english"])))
    (entity,) = resolution.entries.values()
    assert entity.is_new and entity.decision == "new"
    lookups = [args for name, args in fake_graph.calls if name == "get_nodes_by_field_in"]
    assert lookups == [(LANGUAGES, "id", [_english_key()])]
