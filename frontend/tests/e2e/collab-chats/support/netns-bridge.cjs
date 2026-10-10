#!/usr/bin/env node
// Relays TCP ports between the host's loopback and the Playwright container's own network namespace through Unix
// sockets in a shared directory, so the browser does not need `--network host`.
//
// Why: Chromium fails every in-flight request with net::ERR_NETWORK_CHANGED when an address in its network namespace
// changes. On a shared Docker host every container start or stop adds or removes a veth/bridge address on the host,
// many times a minute, and with `--network host` the browser saw all of them (the app then shows "Network error").
// Inside its own namespace the browser sees only its own interfaces, and nothing on the host is opened to other users.
//
//   node netns-bridge.cjs host  <dir> <port>...                  # on the host: <dir>/<port>.sock -> 127.0.0.1:<port>
//   node netns-bridge.cjs guest <dir> <port>... -- <cmd> [args]  # in the container: 127.0.0.1:<port> -> <dir>/<port>.sock, then runs <cmd>
'use strict';
const net = require('net');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const [mode, dir, ...rest] = process.argv.slice(2);
const sep = rest.indexOf('--');
const ports = (sep === -1 ? rest : rest.slice(0, sep)).map(Number);
const command = sep === -1 ? [] : rest.slice(sep + 1);
if (!['host', 'guest'].includes(mode) || !dir || ports.length === 0 || ports.some((p) => !Number.isInteger(p))) {
  console.error('usage: netns-bridge.cjs host|guest <dir> <port>... [-- <cmd>]');
  process.exit(2);
}

function relay(a, b) {
  a.pipe(b);
  b.pipe(a);
  a.on('error', () => b.destroy());
  b.on('error', () => a.destroy());
}

function listen(server, ...args) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(...args, () => resolve(server));
  });
}

async function main() {
  const servers = [];
  for (const port of ports) {
    const sock = path.join(dir, `${port}.sock`);
    if (mode === 'host') {
      fs.rmSync(sock, { force: true });
      servers.push(await listen(net.createServer((c) => relay(c, net.connect(port, '127.0.0.1'))), sock));
    } else {
      servers.push(await listen(net.createServer((c) => relay(c, net.connect(sock))), port, '127.0.0.1'));
      // The browser resolves `localhost` to ::1 too; a container without IPv6 loopback just skips it.
      await listen(net.createServer((c) => relay(c, net.connect(sock))), port, '::1').then((s) => servers.push(s), () => undefined);
    }
  }
  if (mode === 'host') {
    const stop = () => process.exit(0);
    process.on('SIGTERM', stop);
    process.on('SIGINT', stop);
    return;
  }
  if (command.length === 0) return;
  const child = spawn(command[0], command.slice(1), { stdio: 'inherit' });
  for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => child.kill(signal));
  child.on('exit', (code, signal) => process.exit(code ?? (signal ? 128 : 1)));
}

main().catch((error) => {
  console.error(`netns-bridge ${mode}: ${error.message}`);
  process.exit(2);
});
