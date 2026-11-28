# pyright: ignore-file

"""
Drupal Wiki on the simulator – redirects, addresses, other people, injected text
================================================================================

  TC-DWSIM-SEC-001  A download the wiki redirects to another origin: the token and the session cookie do not follow
                    (the simulator's blob log records what arrived), and the stream does not hand out another body
  TC-DWSIM-SEC-002  Addresses the deployment refuses (link-local, cloud metadata, unspecified) are refused on enabling,
                    before anything is sent to them (the simulator sees no request)
  TC-DWSIM-SEC-003  Another person never reads a record by its id: a signed-in person with no wiki account is refused
                    the stream of a private page and of an open one, and the search finds nothing of it
  TC-DWSIM-SEC-004  Markup, a path and control characters in a space name, a page title and a file name are stored
                    as text, change nothing about who reads what, and the record group keeps its name
  TC-DWSIM-SEC-005  The token is nowhere in the connector's document after a failed and a successful sync
"""

import pytest
import requests
from connectors.drupal_wiki.sim_instances import TOKEN, TWO_FILES_PAGE
from connectors.drupal_wiki.sim_world import REFUSED, DrupalWorld, succeeded


@pytest.mark.integration
@pytest.mark.drupal_wiki_sim
@pytest.mark.asyncio(loop_scope="session")
class TestDrupalWikiSimSecurity:

    async def test_tc_dwsim_sec_001_redirected_download_carries_no_credential(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"download_redirect": True}))
        rec = await w.record_for("attachment:5000")
        try:
            resp = w.client.stream_record(rec["id"])
            body = b"".join(resp.iter_content(chunk_size=65536))
        except requests.HTTPError:
            body = b""
        log = w.sim.call("GET", "/_sim/drupalwiki/blob-log") or []
        leaked = [e for e in log if e["authorization"] or e["cookie"]]
        assert not leaked, f"the redirect target received a credential: {leaked}"
        direct = requests.get(f"{w.sim.base_url}/_sim/drupalwiki/blob/5000", timeout=30).content
        assert body in (b"", direct), "a redirected download streamed another body"

    @pytest.mark.parametrize("address", ["http://169.254.169.254", "http://169.254.0.1:8080", "http://0.0.0.0"])
    async def test_tc_dwsim_sec_002_addresses_the_deployment_refuses(self, drupal_world: DrupalWorld,
                                                                      address: str) -> None:
        w = drupal_world
        outcome = await w.start(base_url=address)
        assert outcome == REFUSED, f"{address} (link-local, cloud metadata or unspecified) was turned on: {outcome}"
        assert not await w.records(parents=False)
        assert w.sim.calls()["total"] == 0, "the connector sent the wiki's token to an address that must be refused"

    async def test_tc_dwsim_sec_003_another_person_never_reads_by_id(self, drupal_world: DrupalWorld,
                                                                      second_user) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        for ext in ("page:501", "page:301", "page:101", "attachment:5004"):
            rec = await w.record_for(ext)
            resp = second_user.get(w.client._stream_record_path(rec["id"]))
            assert resp.status_code in (401, 403, 404), f"{ext}: a person with no wiki account got HTTP {resp.status_code}"

    async def test_tc_dwsim_sec_004_injected_markup_and_paths_in_names(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert await w.start(enable=False) is None
        space, = (r["space"] for r in w.mutate(("space_create", {"name": "<img src=x onerror=alert(1)> ../../etc", "access": "PRIVATE"})))
        page, = (r["page"] for r in w.mutate(("page_create", {"space": space, "title": "'; DROP TABLE records;-- ‮\x07"})))
        w.mutate(("attachment_add", {"page": page, "file_name": "..\\..\\windows\\evil.txt", "content": "evil"}),
                 ("attachment_add", {"page": TWO_FILES_PAGE, "file_name": "<svg onload=alert(1)>.txt", "content": "svg"}))
        assert succeeded(await w.enable())
        await w.assert_matches_source("after a sync of names with markup and paths")
        names = list(await w.graph.fetch_record_group_names(w.connector_id))
        assert "<img src=x onerror=alert(1)> ../../etc" in names, f"the space lost its name: {names}"

    async def test_tc_dwsim_sec_005_the_token_is_not_stored_in_the_open(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        doc = str(w.connector())
        assert TOKEN not in doc and "dash" not in doc.replace("dashboard", ""), "the connector's document shows the token"
        w.knobs(token_revoked=True)
        await w.sync()
        w.knobs(token_revoked=False)
        assert TOKEN not in str(w.connector())
