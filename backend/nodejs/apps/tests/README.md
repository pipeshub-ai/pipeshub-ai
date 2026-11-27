# Node.js Backend Unit Tests

Unit tests for the PipesHub Node.js backend service using **Mocha** (test runner) and **c8** (code coverage).

## Prerequisites

```bash
cd backend/nodejs/apps
npm install
```

## Running Tests

```bash
# Run all unit tests (parallel, 4 workers). Reports coverage; fails only on the
# unit-only floor for src/modules/*/domain and */application.
npm run test

# Run tests with detailed coverage report (text + lcov + html)
npm run test:coverage

# The coverage gate CI enforces: unit tests, then the MongoDB and Redis itests,
# then the threshold checks on the coverage of both: all of src at .c8rc.json's
# floor, the harness's own code at coverage:harness's. Store adapters
# count through their itests, so do not add unit tests over fake collections
# to raise the figure. Needs a MongoDB replica set and a Redis.
MONGO_IT_URI='mongodb://localhost:27017/itest?replicaSet=rs0&directConnection=true' \
  REDIS_IT_URL=redis://127.0.0.1:6379 npm run test:coverage-gate
```

## Running Specific Tests

```bash
# Run a specific test file
npx mocha --require ts-node/register tests/libs/utils/password.utils.test.ts

# Run all tests in a directory
npx mocha --require ts-node/register 'tests/libs/keyValueStore/**/*.test.ts'

# Run tests matching a grep pattern
npx mocha --require ts-node/register --grep "should encrypt"
```

## Directory Structure

```
tests/
├── setup.ts                          # Global test setup (runs before all tests)
├── app.test.ts                       # App-level integration tests
├── index.test.ts                     # Entry point tests
├── helpers/                          # Shared test utilities
├── utils/                            # Utility function tests
│   ├── generic-functions.test.ts
│   ├── safe-integer.test.ts
│   └── xss-sanitization.test.ts
├── libs/                             # Library tests
│   ├── encryptor/                    # Encryption utility tests
│   ├── enums/                        # Enum definition tests
│   ├── keyValueStore/                # KV store tests (Redis, etcd, in-memory)
│   ├── middlewares/                  # Middleware tests (auth, rate-limit, etc.)
│   └── types/                        # Type validation tests
└── modules/                          # Business module tests
```

## Conventions

- Test files use the `*.test.ts` naming convention
- Tests are written in TypeScript and transpiled via `ts-node` with `tsconfig.test.json`
- Tests run in parallel by default (4 workers) for speed
- Coverage thresholds: **90%** lines/functions/statements, **80%** branches

## Tests against real storage servers (`tests/stores/`)

Files ending in `.itest.ts` talk to a real server instead of mocks, so `npm run test` skips them. The `Storage backends` workflow (`.github/workflows/storage-backends.yml`) runs them against MinIO (S3), Azurite (Azure Blob) and MongoDB, both as a replica set and as a single server. Each file's header lists the environment variables it needs. To run one locally:

```bash
docker run -d --name mongo-rs --ulimit nofile=64000:64000 -p 27017:27017 mongo:8.0.17 mongod --replSet rs0 --bind_ip_all
docker exec mongo-rs mongosh --quiet --eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"localhost:27017"}]})'
MONGO_IT_URI='mongodb://localhost:27017/?replicaSet=rs0' REPLICA_SET_AVAILABLE=true \
  TS_NODE_PROJECT=tsconfig.test.json npx mocha --no-config --require ts-node/register --exit tests/stores/mongo-transactions.itest.ts
```

The agent harness keeps its Mongo suite next to its unit tests: `tests/modules/agent-harness/execution-store.mongo.itest.ts` runs the shared `ExecutionStore` contract (also run in-memory by `execution-store.contract.test.ts`) plus outbox, sweeper and admission cases. It needs a replica set and no `REPLICA_SET_AVAILABLE`:

```bash
MONGO_IT_URI='mongodb://localhost:27017/?replicaSet=rs0&directConnection=true' \
  TS_NODE_PROJECT=tsconfig.test.json npx mocha --no-config --require reflect-metadata \
  --require ts-node/register --exit tests/modules/agent-harness/execution-store.mongo.itest.ts
```

CI runs it through `deployment/docker-compose/tests/agent_harness_replset_test.sh` (`Agent Harness Infrastructure` workflow).

## Coverage Reports

After running `npm run test:coverage`, reports are generated in:
- Terminal: text summary
- `coverage/lcov-report/index.html`: interactive HTML report
- `coverage/lcov.info`: lcov format (for CI integrations)
