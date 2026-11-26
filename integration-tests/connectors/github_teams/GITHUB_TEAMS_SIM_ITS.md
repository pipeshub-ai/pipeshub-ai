# GitHub Teams and GitHub (personal) against the simulator (`github_teams_sim`)

The suite runs the real PipesHub stack against the GitHub simulator of `connectors-simulator` (`connsim`, port 8116). The
connector calls fixed hosts over HTTPS (`api.github.com`, `github.com` and the signed storage host an upload redirects to),
so the stack gets a TLS front that answers those names. Nothing in the product is switched: the connector's test-only
base-URL settings do not exist in this tree, and the aliases need none.

## Run it

```bash
deployment/docker-compose/sim-tls/make-certs.sh "$SIM_TLS_DIR"       # one CA and certificate for every vendor front
docker compose -f deployment/docker-compose/docker-compose.integration.neo4j.yml \
               -f deployment/docker-compose/docker-compose.integration.github-teams-sim.yml up -d
cd integration-tests && set -a && . connectors/github_teams_sim.env && set +a
pytest -m github_teams_sim connectors/github_teams
```

The suite needs the docker CLI (`github_front` checks where the GitHub names resolve inside the PipesHub container; name the
container with `GITHUB_SIM_APP_CONTAINER` when several stacks run). The live suite of this folder (marker `github_teams`) is
unchanged; the two suites' fixtures do not meet.

## How it fails closed

- `github-tls` takes `api.github.com`, `github.com` and `private-user-images.githubusercontent.com` as network aliases and
  answers with the shared certificate; `pipeshub-ai` trusts its CA through `SSL_CERT_FILE`. The other names of the shared
  certificate (`uploads.github.com`, `raw.githubusercontent.com`, ...) are not aliased: the connector does not call them.
- `github-guard` is healthy only while the three names resolve to the front; `pipeshub-ai` starts after it.
- `github_front` checks the same inside the container before any connector exists (fails, never skips); NET-001 asserts
  that every call the simulator counted names one of the three hosts; NET-002 is the negative control (an unaliased name
  does not resolve to the front); the comparison fails on a request the simulator does not model, a response outside
  GitHub's OpenAPI description, and a token that reached the storage host.

## Why aliases and not `GH_API_BASE_URL` / `GH_WEB_BASE_URL`

The simulator's guide describes those two settings as the way a stack reaches it, but they are not in this tree
(`grep -rn GH_API_BASE_URL` over the repository finds nothing at base 625d04b58). The aliases need no product change and run
the connector's own host checks (it accepts only `https://github.com/user-attachments/` links as attachments) for real.

## Sign-in

Both are offered by the connector and both are covered: a personal access token (`API_TOKEN`; most tests) and OAuth. For
OAuth most tests store the access token as the sign-in would (as the live suite does); AUTH-003 goes through the app's own
authorize and callback routes against the simulator's `github.com`, AUTH-004 gives a wrong client secret. The personal
connector accepts OAuth only.

## What a test asserts

After a sync, the simulator's oracle (`/_sim/github/expected`) says who may read every issue, pull request and file under
GitHub's rules. For every person (a PipesHub account for each: the owner and sync account, a team member, a member with no
grant, an outside collaborator, a GitHub account outside the organisation, `carol@acme.com` with no GitHub account, and
`pipeshub-it@example.com`, the API client that connects every connector) PipesHub's permission query
(`filter_accessible_record_ids`) is compared with it. Types: MISSING, STALE, DUPLICATE, LEAK, DENIED,
LEAK:public-without-account, SIM_UNMODELED, SIM_SPEC, SIM_HOST, SIM_TOKEN_LEAK. The pure comparison and its negative controls
are in `integration-tests/unit/test_github_teams_sim_compare.py`.

The world's clock is the simulator's: it starts two hours behind the wall clock and every sync advances it by three
minutes, so checkpoints (GitHub's `updated_at`) move forward between syncs.

## Known gaps (strict)

| Type / test | What | Where | Open PR |
|---|---|---|---|
| `LEAK:public-without-account` (ACL-015) | a PipesHub user with no GitHub account reads a public repository's records: they are granted to the whole organisation | `github_teams/projects.py` visibility permissions | #6209 |
| SYNC-003 (strict xfail) | a blocker whose blocked issue is closed does not link it: the dependency read is gated on GitHub's open count | `github_teams/issues.py` `_related_from_dependencies` | none |

## Files

`sim_world.py` (world, comparison, fixtures), `sim_instances.py` (worlds), `github_teams_sim_{sync,changes,access,auth,failure,content,filters}_test.py`
and `github_sim_personal_test.py` (docstrings list every case), `connectors/github_teams_sim.env`,
`docker-compose.integration.github-teams-sim.yml`, `github-teams-sim/nginx.conf`, `unit/test_github_teams_sim_compare.py`.

## More cases (`github_teams_sim_extra_test.py`)

An upload in an issue becomes a file record under its issue and indexes (CNT-007); code files and folders get source timestamps after the run ends (CNT-008); the simulator's switches `secondary_limit_status`, `primary_limit_status`, `comment_edit_bumps_issue` and `compare_detects_renames` (SWT-001..004); more than 100 pull requests and comments (SYNC-011).

Not covered: the shared scenario matrix (`connectors/scenario_matrix.py`) has no adapter on the simulator: it needs the live stack's vector, search and storage probes. Its add, edit, rename, delete, full sync, filter and indexing scenarios each have their own case here (CHG-001..003, CHG-005, SYNC-004, FILT-007, FILT-002); only its checks that vectors hold the text and that search finds the record are not run.
