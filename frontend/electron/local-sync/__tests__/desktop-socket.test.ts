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
