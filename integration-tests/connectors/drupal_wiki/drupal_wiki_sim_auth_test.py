# pyright: ignore-file

"""
Drupal Wiki on the simulator – signing in, and what a wiki that refuses does
============================================================================

A personal access token the wiki does not accept, an address that cannot be reached or must not be, and a token whose
owner may not read what the sync needs: PipesHub either refuses to turn the connector on, or the run does not report
success and leaves what it held as it was. Once the credential works again, the next sync matches the wiki. A live
wiki answers an unknown token with a 400 or 403 HTML page, never a 401 (the simulator's ``live_auth``); with
``live_auth`` off it answers 401 ProblemResponses.

  TC-DWSIM-AUTH-001  A wrong token (one with the pat: prefix the wiki does not know, one without it) is refused when the
                     connector is turned on; nothing is synced
  TC-DWSIM-AUTH-002  The same with live_auth off (401)
  TC-DWSIM-AUTH-003  A token revoked after the first sync: the run does not succeed and nothing is lost; the token
                     restored, the next sync matches (both answers of the wiki)
  TC-DWSIM-AUTH-004  A token whose owner may not read users or spaces is refused on enabling; one that may not read
                     groups, pages or attachments fails the run, nothing lost, and recovers
  TC-DWSIM-AUTH-005  An address nothing answers on, a name that does not resolve, and a link-local metadata address
                     are refused on enabling
  TC-DWSIM-AUTH-006  A refused connector given the right token is turned on and matches the wiki
  TC-DWSIM-AUTH-007  An address that does not serve the wiki's API is refused on enabling
"""

import pytest
from connectors.drupal_wiki.sim_instances import TOKEN
from connectors.drupal_wiki.sim_world import REFUSED, DrupalWorld, succeeded


def _refused(outcome: str | None) -> bool:
    return outcome == REFUSED or not succeeded(outcome)


@pytest.mark.integration
@pytest.mark.drupal_wiki_sim
@pytest.mark.asyncio(loop_scope="session")
class TestDrupalWikiSimAuth:

    @pytest.mark.parametrize("live_auth", [True, False])
    @pytest.mark.parametrize("token", ["pat:not-a-token-of-this-wiki", "plain-token-without-prefix"])
    async def test_tc_dwsim_auth_001_002_wrong_token(self, drupal_world: DrupalWorld, token: str,
                                                     live_auth: bool) -> None:
        w = drupal_world
        outcome = await w.start(token=token, knobs={"live_auth": live_auth})
        assert _refused(outcome), f"a wrong token was turned on and its sync reported {outcome}"
        assert not await w.records(parents=False), "a wrong token synced records"

    @pytest.mark.parametrize("live_auth", [True, False])
    async def test_tc_dwsim_auth_003_token_revoked_later(self, drupal_world: DrupalWorld, live_auth: bool) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"live_auth": live_auth}))
        before = await w.holdings()
        w.mutate(("page_create", {"space": 1, "title": "Filed while the token was revoked"}))
        w.knobs(token_revoked=True)
        outcome = await w.sync()
        assert not succeeded(outcome), f"a sync whose every read was refused (live_auth {live_auth}) reported success"
        await w.assert_kept_not_overshared(before, "after a sync with a revoked token")
        w.knobs(token_revoked=False)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the token worked again")

    @pytest.mark.parametrize("endpoint", ["user", "space"])
    async def test_tc_dwsim_auth_004_cannot_read_what_the_connection_needs(self, drupal_world: DrupalWorld,
                                                                          endpoint: str) -> None:
        w = drupal_world
        outcome = await w.start(knobs={"forbidden_endpoints": [endpoint]})
        assert _refused(outcome), f"a token that may not read {endpoint}s was turned on and reported {outcome}"
        assert not await w.records(parents=False)
        w.client.toggle_sync(w.connector_id, enable=False)
        w.knobs(forbidden_endpoints=[])
        assert succeeded(await w.enable())
        await w.assert_matches_source("after the token could read again")

    @pytest.mark.parametrize("endpoint", ["group", "page", "attachment"])
    async def test_tc_dwsim_auth_004_cannot_read_what_the_sync_needs(self, drupal_world: DrupalWorld,
                                                                    endpoint: str) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.mutate(("page_create", {"space": 1, "title": "Filed while reading was refused"}))
        w.knobs(forbidden_endpoints=[endpoint])
        outcome = await w.sync()
        assert not succeeded(outcome), f"a sync whose {endpoint} reads were refused (403) reported success"
        await w.assert_kept_not_overshared(before, f"after a sync that could not read {endpoint}s")
        w.knobs(forbidden_endpoints=[])
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the token could read again")

    @pytest.mark.parametrize("address", ["http://drupal-wiki-sim.internal:9", "http://no-such-wiki.internal",
                                         "http://169.254.169.254"])
    async def test_tc_dwsim_auth_005_unreachable_or_refused_address(self, drupal_world: DrupalWorld,
                                                                    address: str) -> None:
        w = drupal_world
        outcome = await w.start(base_url=address)
        assert _refused(outcome), f"{address} was turned on and its sync reported {outcome}"
        assert not await w.records(parents=False)

    async def test_tc_dwsim_auth_006_credential_recovering(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert _refused(await w.start(token="pat:wrong"))
        w.client.toggle_sync(w.connector_id, enable=False)
        w.set_token(TOKEN)
        assert succeeded(await w.enable())
        await w.assert_matches_source("after the right token was saved")

    async def test_tc_dwsim_auth_007_a_login_page_instead_of_the_wiki(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        # A path of the simulator that serves no wiki API: whatever it answers is not the wiki's user listing.
        outcome = await w.start(base_url="http://drupal-wiki-sim.internal/_sim/drupalwiki/blob")
        assert _refused(outcome), f"an address that does not serve the wiki's API was turned on ({outcome})"
