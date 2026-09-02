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

function attachSocket(client: DesktopSocketClient, socket: FakeSocket): void {
  (client as unknown as { socket: FakeSocket | null }).socket = socket;
}

function makeClient(listConnectorIds: () => string[] = () => ['c-1']) {
  const registrations: DesktopRegisterAck[] = [];
  const client = new DesktopSocketClient({
    credentials: { deviceId: 'dev-1' } as DesktopCredentialsStore,
    listConnectorIds,
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
    onRegistration: (ack) => registrations.push(ack),
    log: () => { /* quiet */ },
  });
  return { client, registrations };
}

test('register resolves only after the gateway ack', async () => {
  const { client, registrations } = makeClient();
  let ackFn: EmitAck | undefined;
  attachSocket(client, {
    connected: true,
    emit: (_event, _payload, ack) => {
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
  assert.ok(ackFn, 'must emit desktop:register with an ack callback');

  ackFn!({ accepted: ['c-1'], rejected: [] });
  const ack = await pending;
  assert.deepEqual(ack, { accepted: ['c-1'], rejected: [] });
  assert.deepEqual(registrations, [{ accepted: ['c-1'], rejected: [] }]);
});

test('register returns null when the socket is not connected and never connects', async () => {
  const { client, registrations } = makeClient();
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
  assert.equal(registrations.length, 0);
});
