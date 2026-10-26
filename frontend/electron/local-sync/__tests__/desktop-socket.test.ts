import test from 'node:test';
import * as assert from 'node:assert';
import { DesktopSocketClient, type DesktopRegisterAck } from '../transport/desktop-socket';
import type { DesktopCredentialsStore } from '../persistence/credentials';
import type { ServePullRequest } from '../pull-responder-types';

type EmitAck = (ack: DesktopRegisterAck | undefined) => void;

interface FakeSocket {
  connected: boolean;
  emit: (
    event: string,
    payload: unknown,
    ack?: EmitAck,
  ) => void;
  once: (event: string, listener: () => void) => void;
  off: (event: string, listener: () => void) => void;
}

const CREDENTIALS = { deviceId: 'dev-1', deviceName: 'host-1' } as DesktopCredentialsStore;

function attachSocket(client: DesktopSocketClient, socket: FakeSocket): void {
  (client as unknown as { socket: FakeSocket | null }).socket = socket;
}

function makeClient(logs: string[] = []): DesktopSocketClient {
  return new DesktopSocketClient({
    credentials: CREDENTIALS,
    servePull: async (request: ServePullRequest) => ({
      ok: false as const,
      runId: request.runId,
      batchIndex: request.batchIndex,
      error: { code: 'INTERNAL', message: 'unused', retryable: true },
    }),
    serveContent: async () => ({
      ok: false as const,
      requestId: 'unused',
      error: { code: 'INTERNAL', message: 'unused', retryable: true },
    }),
    log: (message) => logs.push(message),
  });
}

test('register announces the device and resolves only after the gateway ack', async () => {
  const client = makeClient();
  const emitted: Array<{ event: string; payload: unknown }> = [];
  let ackFn: EmitAck | undefined;
  attachSocket(client, {
    connected: true,
    emit: (event, payload, ack) => {
      emitted.push({ event, payload });
      ackFn = ack;
    },
    once: () => { /* unused */ },
    off: () => { /* unused */ },
  });

  let resolved: DesktopRegisterAck | null | undefined;
  const pending = client.register(1_000).then((ack) => {
    resolved = ack;
    return ack;
  });

  await Promise.resolve();
  assert.equal(resolved, undefined, 'must not resolve before the ack');
  assert.deepEqual(emitted, [
    { event: 'desktop:register', payload: { deviceId: 'dev-1', deviceName: 'host-1' } },
  ]);
  assert.ok(ackFn, 'must emit desktop:register with an ack callback');

  ackFn!({ ok: true });
  assert.deepEqual(await pending, { ok: true });
});

test('a refused registration is logged and returned', async () => {
  const logs: string[] = [];
  const client = makeClient(logs);
  attachSocket(client, {
    connected: true,
    emit: (_event, _payload, ack) => ack?.({ ok: false, reason: 'MISSING_DEVICE_ID' }),
    once: () => { /* unused */ },
    off: () => { /* unused */ },
  });

  const ack = await client.register(1_000);
  assert.deepEqual(ack, { ok: false, reason: 'MISSING_DEVICE_ID' });
  assert.ok(logs.some((line) => line.includes('MISSING_DEVICE_ID')), `expected a log, got ${JSON.stringify(logs)}`);
});

test('a pull that throws still names the answering device', async () => {
  const client = new DesktopSocketClient({
    credentials: CREDENTIALS,
    servePull: async () => {
      throw new Error('boom');
    },
    serveContent: async () => ({
      ok: false as const,
      requestId: 'unused',
      error: { code: 'INTERNAL', message: 'unused', retryable: true },
    }),
    log: () => { /* quiet */ },
  });
  const acks: Array<Record<string, unknown>> = [];
  const handlePull = (client as unknown as {
    handlePull: (request: ServePullRequest, ack: (response: Record<string, unknown>) => void) => Promise<void>;
  }).handlePull.bind(client);
  const request: ServePullRequest = {
    connectorId: 'c-1',
    runId: 'r-1',
    batchIndex: 0,
    mode: 'INCREMENTAL',
    maxEvents: 10,
  };

  await handlePull(request, (response) => acks.push(response));

  assert.equal(acks.length, 1);
  assert.equal(acks[0].ok, false);
  assert.equal((acks[0].error as { code: string }).code, 'INTERNAL');
  assert.equal(acks[0].deviceId, 'dev-1');
});

type Listener = (...args: unknown[]) => void;

interface ListenedSocket {
  active: boolean;
  connected: boolean;
  connects: number;
  handlers: Map<string, Listener>;
  on: (event: string, listener: Listener) => void;
  connect: () => void;
  removeAllListeners: () => void;
  disconnect: () => void;
  io: { opts: { reconnection: boolean } };
}

function listenedSocket(): ListenedSocket {
  const socket: ListenedSocket = {
    active: false,
    connected: false,
    connects: 0,
    handlers: new Map(),
    on: (event, listener) => socket.handlers.set(event, listener),
    connect: () => { socket.connects += 1; },
    removeAllListeners: () => socket.handlers.clear(),
    disconnect: () => { /* unused */ },
    io: { opts: { reconnection: true } },
  };
  return socket;
}

/** Wires the client's listeners onto a fake socket and captures the timers it sets. */
function withListeners(socket: ListenedSocket) {
  const client = makeClient();
  attachSocket(client, socket as unknown as FakeSocket);
  (client as unknown as { attachListeners: (s: ListenedSocket) => void }).attachListeners(socket);
  const timers: Array<{ fn: () => void; delay: number }> = [];
  const realSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = ((fn: () => void, delay: number) => {
    timers.push({ fn, delay });
    return timers.length as unknown as ReturnType<typeof setTimeout>;
  }) as typeof setTimeout;
  const restore = () => { globalThis.setTimeout = realSetTimeout; };
  const refuse = (message: string) => socket.handlers.get('connect_error')!(new Error(message));
  return { client, timers, refuse, restore };
}

test('a handshake refused for a non-auth reason is retried with a growing delay', () => {
  const socket = listenedSocket();
  const { timers, refuse, restore } = withListeners(socket);
  try {
    refuse('Unable to check the session right now');
    assert.equal(timers.length, 1);
    assert.equal(timers[0].delay, 2_000);
    timers[0].fn();
    assert.equal(socket.connects, 1);

    refuse('Unable to check the session right now');
    assert.equal(timers[1].delay, 4_000);
  } finally {
    restore();
  }
});

test('a handshake refused as an auth failure is not retried', () => {
  const socket = listenedSocket();
  const { timers, refuse, restore } = withListeners(socket);
  try {
    refuse('Authentication refused: User not found, please login again');
    assert.equal(timers.length, 0);
  } finally {
    restore();
  }
});

test('a transport error is left to socket.io, which is still reconnecting', () => {
  const socket = listenedSocket();
  socket.active = true;
  const { timers, refuse, restore } = withListeners(socket);
  try {
    refuse('websocket error');
    assert.equal(timers.length, 0);
  } finally {
    restore();
  }
});

test('disconnect cancels a pending handshake retry', () => {
  const socket = listenedSocket();
  const { client, timers, refuse, restore } = withListeners(socket);
  try {
    refuse('Unable to check the session right now');
    client.disconnect();
    timers[0].fn();
    assert.equal(socket.connects, 0);
  } finally {
    restore();
  }
});

test('register returns null when the socket is not connected and never connects', async () => {
  const client = makeClient();
  attachSocket(client, {
    connected: false,
    emit: () => {
      assert.fail('must not emit register while disconnected');
    },
    once: () => { /* never connects */ },
    off: () => { /* unused */ },
  });

  const ack = await client.register(20);
  assert.equal(ack, null);
});
