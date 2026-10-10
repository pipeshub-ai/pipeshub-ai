// Preloaded into the lane's Node API (NODE_OPTIONS=--require): the product has no scrape endpoint,
// so this serves the in-process prom registry on COLLAB_METRICS_PROBE_PORT for the load bench.
const http = require('http');

const port = Number(process.env.COLLAB_METRICS_PROBE_PORT || 0);

function registry() {
  for (const [file, mod] of Object.entries(require.cache)) {
    if (/[\\/]libs[\\/]services[\\/]telemetry[\\/]metrics-backend\.(ts|js)$/.test(file) && mod.exports.metricsBackend) {
      return mod.exports.metricsBackend;
    }
  }
  return undefined;
}

if (port > 0) {
  const server = http.createServer(async (_req, res) => {
    const backend = registry();
    if (!backend) {
      res.statusCode = 503;
      res.end('metrics backend not loaded yet');
      return;
    }
    res.setHeader('content-type', 'text/plain; version=0.0.4');
    res.end(await backend.serialize());
  });
  server.on('error', () => {});
  server.listen(port, '127.0.0.1');
  server.unref();
}
