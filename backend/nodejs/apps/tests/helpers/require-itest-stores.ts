// The coverage gate counts the store adapters through these itests, and they skip themselves without a
// store: a run with one missing would pass the tests and then fail the gate with nothing to explain it.
const missing = ['MONGO_IT_URI', 'REDIS_IT_URL'].filter((name) => !process.env[name]);
if (missing.length > 0) {
  console.error(
    `test:itest needs ${missing.join(' and ')}: a MongoDB replica set and a Redis, for example\n` +
      "  MONGO_IT_URI='mongodb://localhost:27017/itest?replicaSet=rs0&directConnection=true' " +
      'REDIS_IT_URL=redis://127.0.0.1:6379 npm run test:coverage-gate',
  );
  process.exit(2);
}
