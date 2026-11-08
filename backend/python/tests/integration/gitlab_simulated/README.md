# GitLab connector against a simulated GitLab

The real GitLab connector syncs from a simulated GitLab into a real Neo4j and a real
ArangoDB. Only GitLab, the message broker and the config store are simulated.

- `glsim/`: the simulator. Every request is checked against GitLab's OpenAPI document
  (`specs/openapi_gitlab_subset.json`) and every reply against its schema. A field or
  operation the simulator does not model fails the test that used it.
- `world.py`: one PipesHub org wired to one simulated GitLab. `diff()` compares what each
  user can read in PipesHub (the search permission query) with what GitLab would show them.
- `test_gitlab_sim_*.py`: sync, deletion, access, resilience, security, content, and attacks
  on the fixes.

Run (needs the integration graph stack, see `tests/integration/real_graph.py`):

```bash
docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml up -d
cd backend/python && pytest tests/integration/gitlab_simulated -m integration
GITLAB_SIM_TRASH=1 pytest tests/integration/gitlab_simulated -m integration   # with the trash on
```

CI: `backend-matrix` runs it on both stores (the `gitlab` jobs, trash off) on pull
requests that touch the connector, its clients or the platform code under it, and
nightly on main. Any skip fails the job; the strict expected failure does not.

Where each simulated behaviour comes from (docs, GitLab's source, live checks) is listed
in `SOURCES.md`, with the confidence of each. When the connector starts using a field or
an endpoint the simulator does not model, the test fails as a gap: model it here from
GitLab's docs, and add the source to `SOURCES.md`.
