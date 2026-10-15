# Graph DB integration tests

These tests run the graph providers against a real Neo4j and a real ArangoDB. They never guess a host: with no configuration they skip and name the variables they need.

## Environment

| Variable | Meaning | Default |
| --- | --- | --- |
| `PCC_NEO4J_URI` | Bolt URI, e.g. `bolt://127.0.0.1:<port>` | none (required) |
| `PCC_NEO4J_PASSWORD` | Neo4j password | none (required) |
| `PCC_NEO4J_USER` | Neo4j user | `neo4j` |
| `PCC_ARANGO_URL` | ArangoDB base URL | none (required) |
| `PCC_ARANGO_PASSWORD` | ArangoDB root password | none (required) |
| `PCC_ARANGO_USER` | ArangoDB user | `root` |
| `PCC_ARANGO_DB` | Database for tests that do not use a dedicated one | per-test (`es`) |
| `PCC_GATE` | `1` makes an unconfigured or unreachable backend a failure instead of a skip | unset |

The old names (`NEO4J_IT_*`, `NEO4J_TEST_*`, `ARANGO_IT_*`, `ARANGO_TEST_*`) still work for one release and emit a `DeprecationWarning`. The mapping lives in `_backends.py`.

## Run against your own throwaway containers

Pick random loopback ports and a throwaway password, so you never collide with someone else's stack on a shared host:

```bash
NEO_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
ARANGO_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
PW=$(openssl rand -hex 8)

docker run -d --name my-graph-neo4j -p 127.0.0.1:$NEO_PORT:7687 -e NEO4J_AUTH=neo4j/$PW neo4j:5.26.0
docker run -d --name my-graph-arango -p 127.0.0.1:$ARANGO_PORT:8529 -e ARANGO_ROOT_PASSWORD=$PW arangodb:3.12.4

export PCC_NEO4J_URI=bolt://127.0.0.1:$NEO_PORT PCC_NEO4J_PASSWORD=$PW
export PCC_ARANGO_URL=http://127.0.0.1:$ARANGO_PORT PCC_ARANGO_PASSWORD=$PW

cd backend/python
pytest tests/integration/graph_db -m integration

docker rm -f my-graph-neo4j my-graph-arango
```

`deployment/docker-compose/docker-compose.integration.graph-db.yml` (repository root) is an alternative; export the same `PCC_*` variables for the ports it publishes (17687 for Neo4j, 18529 for ArangoDB, password `ensure-it-pass`).

Wait for both servers to accept connections before the first run (Neo4j takes about 20 seconds).

## Gate mode

`PCC_GATE=1` is for CI gates: a missing variable or an unreachable server fails the test, so a gate cannot pass by skipping everything.

`test_backend_env.py` covers this convention and needs no database.
