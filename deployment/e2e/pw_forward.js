// The Playwright container's loopback ports, forwarded to the stack's services on its own network (QA-AUTO F4).
//
//   node pw_forward.js 37263=pipeshub-ai:3000 52135=pxh-fake-llm:8080 ...
//
// The container runs in the stack's network namespace, not the host's: on the host's, Chromium sees every Docker
// network another lane makes or removes as an IP address change and fails its requests in flight with
// net::ERR_NETWORK_CHANGED (a page that never boots, a chunk that never loads). Forwarding keeps every URL a spec and
// the app use (http://127.0.0.1:<host port>), so the origin stays a secure context and nothing else changes.
'use strict';
const net = require('net');

const pairs = process.argv.slice(2).map((arg) => {
  const m = /^(\d+)=([^:]+):(\d+)$/.exec(arg);
  if (!m) throw new Error(`pw_forward: expected <port>=<host>:<port>, got ${arg}`);
  return { listen: Number(m[1]), host: m[2], port: Number(m[3]) };
});

let ready = 0;
for (const { listen, host, port } of pairs) {
  const server = net.createServer((client) => {
    const upstream = net.connect(port, host);
    client.pipe(upstream).pipe(client);
    const close = () => { client.destroy(); upstream.destroy(); };
    client.on('error', close);
    upstream.on('error', close);
  });
  server.on('error', (error) => {
    process.stderr.write(`pw_forward: 127.0.0.1:${listen} -> ${host}:${port}: ${error.message}\n`);
    process.exit(1);
  });
  server.listen(listen, '127.0.0.1', () => {
    ready += 1;
    if (ready === pairs.length) process.stdout.write(`pw_forward: ready (${pairs.length} ports)\n`);
  });
}
