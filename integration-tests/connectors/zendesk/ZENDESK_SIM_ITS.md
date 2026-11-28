# Zendesk against the simulator (`zendesk_sim`)

The suite runs the real PipesHub stack against the Zendesk simulator of `connectors-simulator` (`connsim`, port 8115). The
connector calls fixed hosts over HTTPS (`https://<subdomain>.zendesk.com` and the attachment CDN, `*.zdusercontent.com`),
so the stack gets a TLS front that answers those names; nothing in the product is switched.

## Run it

```bash
deployment/docker-compose/sim-tls/make-certs.sh "$SIM_TLS_DIR"       # one CA and certificate for every vendor front
docker compose -f deployment/docker-compose/docker-compose.integration.neo4j.yml \
               -f deployment/docker-compose/docker-compose.integration.zendesk-sim.yml up -d
cd integration-tests && set -a && . connectors/zendesk_sim.env && set +a
pytest -m zendesk_sim connectors/zendesk
```

The suite needs the docker CLI (`zendesk_front` checks where the Zendesk names resolve inside the PipesHub container;
name the container with `ZENDESK_SIM_APP_CONTAINER` when several stacks run). The live suite of this folder (marker
`zendesk`) is unchanged; its fixtures and this suite's do not meet (`ai_models_configured` in `conftest.py` seeds models
only when `ZENDESK_SIM_URL` is set).

## How it fails closed

- `zendesk-tls` takes `pipeshubsim.zendesk.com`, `pipeshubsim.zdusercontent.com` and `p1.zdusercontent.com` as network
  aliases and answers with the shared certificate; `pipeshub-ai` trusts its CA through `SSL_CERT_FILE`.
- `zendesk-guard` is healthy only while the three names resolve to the front; `pipeshub-ai` starts after it.
- `zendesk_front` checks the same inside the container before any connector exists (fails, never skips); AUTH-007 asserts
  every call was counted by the simulator and none carried another host or a bearer token to the CDN; AUTH-008 is the
  negative control (a Zendesk name nobody aliased does not resolve to the front).
- The scenario's `public_base` and `cdn_base` are https URLs the front answers, so the connector's own checks "this is
  the tenant's host" (bearer token allowed) and "this is a Zendesk CDN host" (no token) run for real.

## Sign-in

OAuth. Most tests store the account's own token pair as the sign-in would (`inject_connector_credentials`, as the
Salesforce suite does). AUTH-001/002 go through the app's authorize and callback routes (PKCE-less authorization code);
AUTH-003/004 revoke the access token (PipesHub refreshes it and stores the replaced refresh token: rotation on and off)
and both tokens (the run fails, nothing is lost, a new sign-in recovers).

## What a test asserts

After a sync, the simulator's oracle (`/_sim/zendesk/expected`) says who may read every ticket, file and article under
Zendesk's rules. For every person (each has a PipesHub account with the same address, plus `outsider@corp.example` with no
Zendesk account and `pipeshub-it@example.com`, the suite's API client, a Zendesk admin) PipesHub's permission query
(`filter_accessible_record_ids`) is compared with it. Types: MISSING, STALE, DUPLICATE, PARENT, LEAK:*, DENIED:*,
SIM_UNMODELED, SIM_SPEC, SIM_CDN_LEAK, SIM_WRONG_HOST. The pure comparison and its negative controls are in
`integration-tests/unit/test_zendesk_sim_compare.py`.

Scope of a sync: tickets that are not deleted; files on public comments that are not embedded images; published
articles with no user segment, and their non-inline files. Filters: group (in / not in, `unassigned_tickets`), created,
modified; indexing filters keep the records.

## Known gaps (strict)

| Type | What | Where |
|---|---|---|
| `DENIED:article-unregistered` | a workspace member the connector does not register (no Zendesk account, or an end user none of whose tickets is in the sync) cannot find a public article: the connector grants the whole org and never links the org's All team to the app | `connector.py` `_article_permissions`, no `ensure_team_app_edge`; ACL-001 is the strict xfail |

`GET help_center/articles/{id}/comments` carries `sort_order`, which the Help Center reference does not declare: listed in
`KNOWN_REQUEST_FINDINGS`, every other spec problem fails the test.

## Files

`sim_world.py` (world, comparison, fixtures), `sim_instances.py` (accounts), `zendesk_sim_{sync,changes,access,auth,failure,content}_test.py`
(docstrings list every case), `connectors/zendesk_sim.env`, `docker-compose.integration.zendesk-sim.yml`,
`zendesk-sim/nginx.conf`, `unit/test_zendesk_sim_compare.py`.
