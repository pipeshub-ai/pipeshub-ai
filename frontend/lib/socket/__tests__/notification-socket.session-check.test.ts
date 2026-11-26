/**
 * The web notification socket when the server refuses the handshake because it
 * is temporarily unable to check the session: it retries with backoff (as the
 * desktop socket does) instead of staying off until a reload, never retries an
 * auth refusal, and leaves no timer or listener behind. `socket.io-client` is
 * faked; the refusals are the ones the real gateways send, recorded in the
 * backend contract fixture that tests/contract/socket-handshake-refusals.test.ts
 * keeps in step with the server.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type WireRefusal = { message: string; data?: unknown };
const recorded = JSON.parse(
  readFileSync(
    resolve(__dirname, '../../../../backend/nodejs/apps/tests/contract/socket-handshake-refusals.fixture.json'),
    'utf8',
  ),
) as { codes: string[]; notification: Record<string, WireRefusal>; desktopProxy: Record<string, WireRefusal> };
const gateways = { notification: recorded.notification, desktopProxy: recorded.desktopProxy };
/** Every refusal code the server defines, except the one temporary code. */
const NOT_TEMPORARY_CODES = recorded.codes.filter((code) => code !== 'SESSION_CHECK_UNAVAILABLE');

/** What socket.io-client emits for a CONNECT_ERROR packet (build/esm/socket.js, `case PacketType.CONNECT_ERROR`). */
function connectError(refusal: WireRefusal): Error {
  return Object.assign(new Error(refusal.message), { data: refusal.data });
}

/** The same refusal from a server that sends no `data` (before the structured code). */
function withoutData(refusal: WireRefusal): Error {
  return connectError({ message: refusal.message });
}

type Handler = (...args: unknown[]) => void;

class FakeSocket {
  connected = false;
  /** socket.io sets this false once a refused handshake ends its own reconnecting. */
  active = true;
  handlers = new Map<string, Handler[]>();
  connect = vi.fn(() => this);
  disconnect = vi.fn(() => this);
  constructor(public options: Record<string, unknown>) {}
  on(event: string, handler: Handler) {
    this.handlers.set(event, [...(this.handlers.get(event) ?? []), handler]);
    return this;
  }
  off(event: string, handler: Handler) {
    this.handlers.set(event, (this.handlers.get(event) ?? []).filter((h) => h !== handler));
    return this;
  }
  removeAllListeners = vi.fn(() => {
    this.handlers.clear();
    return this;
  });
  emitLocal(event: string, ...args: unknown[]) {
    for (const handler of this.handlers.get(event) ?? []) handler(...args);
  }
  listenerCount() {
    return [...this.handlers.values()].reduce((n, list) => n + list.length, 0);
  }
}

const created: FakeSocket[] = [];
vi.mock('socket.io-client', () => ({
  io: (_origin: string, options: Record<string, unknown>) => {
    const socket = new FakeSocket(options);
    created.push(socket);
    return socket;
  },
}));
vi.mock('@/lib/utils/api-base-url', () => ({ getApiBaseUrl: () => 'https://api.example.test' }));

const mod = await import('../notification-socket');

const REFUSED = connectError(gateways.notification.sessionCheckUnavailable);
const AUTH_REFUSALS = Object.entries(gateways)
  .flatMap(([gateway, refusals]) => Object.entries(refusals).map(([name, refusal]) => [`${gateway}.${name}`, refusal] as const))
  .filter(([name]) => !name.endsWith('.sessionCheckUnavailable'));

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(Math, 'random').mockReturnValue(1); // full delay, no jitter reduction
  created.length = 0;
});

afterEach(() => {
  mod.disconnectNotificationSocket();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function refuse(socket: FakeSocket, error: Error = REFUSED) {
  socket.active = false;
  socket.emitLocal('connect_error', error);
}

describe('a handshake refused because the session cannot be checked', () => {
  it('reconnects after a delay instead of staying off', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];

    refuse(socket);
    expect(socket.connect).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MS - 1);
    expect(socket.connect).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);

    expect(socket.connect).toHaveBeenCalledTimes(1);
  });

  it('backs off exponentially up to the cap, and starts over after a connection', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    const delays: number[] = [];

    for (let i = 0; i < 8; i += 1) {
      refuse(socket);
      const before = socket.connect.mock.calls.length;
      let waited = 0;
      while (socket.connect.mock.calls.length === before && waited < 120_000) {
        await vi.advanceTimersByTimeAsync(500);
        waited += 500;
      }
      delays.push(waited);
    }

    expect(delays[0]).toBe(2_000);
    expect(delays[1]).toBe(4_000);
    expect(delays[2]).toBe(8_000);
    expect(delays.at(-1)).toBe(mod.RECONNECT_DELAY_MAX_MS);

    socket.connected = true;
    socket.emitLocal('connect');
    socket.connected = false;
    refuse(socket);
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MS);
    expect(socket.connect.mock.calls.length).toBe(9);
  });

  it('keeps one pending retry however many refusals arrive', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    refuse(socket);
    refuse(socket);
    refuse(socket);
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MS);
    expect(socket.connect).toHaveBeenCalledTimes(1);
  });

  it('does not retry a socket that connected meanwhile', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    refuse(socket);
    expect(vi.getTimerCount()).toBe(1); // a retry is pending
    socket.connected = true;
    socket.emitLocal('connect');
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MAX_MS);
    expect(socket.connect).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('other connection errors', () => {
  it('does not retry by hand while socket.io is still reconnecting by itself, even for the session-check refusal', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];

    socket.active = true; // a transport hiccup that carried the same text: socket.io has its own retry running
    socket.emitLocal('connect_error', REFUSED);

    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MAX_MS);
    expect(socket.connect).not.toHaveBeenCalled();
  });

  it('leaves a network error to socket.io, which retries with the same backoff bounds', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];

    socket.active = true; // transport error: socket.io is still reconnecting by itself
    socket.emitLocal('connect_error', new Error('websocket error'));
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MAX_MS);

    expect(socket.connect).not.toHaveBeenCalled(); // no second, competing retry
    expect(socket.options).toMatchObject({
      reconnection: true,
      reconnectionDelay: mod.RECONNECT_DELAY_MS,
      reconnectionDelayMax: mod.RECONNECT_DELAY_MAX_MS,
    });
  });

  it('does not retry a refused token by hand: the refreshed token reconnects', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    refuse(socket, connectError(gateways.notification.tokenInvalid));
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MAX_MS * 2);
    expect(socket.connect).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('what the server says about retrying', () => {
  it('has a recorded session-check refusal that says it is retryable', () => {
    expect(gateways.notification.sessionCheckUnavailable.data).toEqual({ code: 'SESSION_CHECK_UNAVAILABLE', retryable: true });
    expect(AUTH_REFUSALS.length).toBeGreaterThanOrEqual(5);
  });

  it.each(AUTH_REFUSALS)('never retries the auth refusal %s', async (_name, refusal) => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    refuse(socket, connectError(refusal));
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MAX_MS * 2);
    expect(socket.connect).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('retries the desktop gateway session-check refusal too', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    refuse(socket, connectError(gateways.desktopProxy.sessionCheckUnavailable));
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MS);
    expect(socket.connect).toHaveBeenCalledTimes(1);
  });

  it('goes by data, not text: a refusal marked not retryable is not retried even with the session-check text', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    refuse(socket, connectError({ ...gateways.notification.sessionCheckUnavailable, data: { code: 'AUTH_SESSION_REFUSED', retryable: false } }));
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MAX_MS * 2);
    expect(socket.connect).not.toHaveBeenCalled();
  });

  it('goes by data, not text: a refusal marked retryable is retried whatever its text', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    refuse(socket, connectError({ message: 'Session store is restarting', data: gateways.notification.sessionCheckUnavailable.data }));
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MS);
    expect(socket.connect).toHaveBeenCalledTimes(1);
  });

  it('knows every code the server defines, and only one of them is temporary', () => {
    expect(recorded.codes).toContain('SESSION_CHECK_UNAVAILABLE');
    expect(NOT_TEMPORARY_CODES.length).toBe(recorded.codes.length - 1);
    expect(NOT_TEMPORARY_CODES.length).toBeGreaterThanOrEqual(4);
  });

  // An auth or sign-out code wins over `retryable`: a server or proxy that gets the flag wrong must not cause endless retries.
  it.each(NOT_TEMPORARY_CODES)('never retries a refusal with code %s, even when it says retryable: true', async (code) => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    refuse(socket, connectError({ ...gateways.notification.sessionCheckUnavailable, data: { code, retryable: true } }));
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MAX_MS * 2);
    expect(socket.connect).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    ['an unknown code', { code: 'SOMETHING_NEW', retryable: true }],
    ['no code', { retryable: true }],
    ['the temporary code without retryable', { code: 'SESSION_CHECK_UNAVAILABLE' }],
    ['the temporary code with retryable as a string', { code: 'SESSION_CHECK_UNAVAILABLE', retryable: 'true' }],
  ])('does not retry a refusal with %s', async (_case, data) => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    refuse(socket, connectError({ ...gateways.notification.sessionCheckUnavailable, data }));
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MAX_MS * 2);
    expect(socket.connect).not.toHaveBeenCalled();
  });

  it.each([
    (text: string) => `Authentication refused: ${text}`,
    (text: string) => `Invalid token: ${text.toLowerCase()}`,
    (text: string) => `${text} for this token`,
    (text: string) => `${text}.`,
    (text: string) => text.toUpperCase(),
  ])('with an older server, does not retry a message that only contains the session-check text (%#)', async (variant) => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    refuse(socket, connectError({ message: variant(gateways.notification.sessionCheckUnavailable.message) }));
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MAX_MS * 2);
    expect(socket.connect).not.toHaveBeenCalled();
  });

  it('with an older server, retries the exact session-check text, ignoring surrounding whitespace', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    refuse(socket, connectError({ message: `  ${gateways.notification.sessionCheckUnavailable.message}\n` }));
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MS);
    expect(socket.connect).toHaveBeenCalledTimes(1);
  });

  it('with an older server that sends no data, retries the session-check text and nothing else', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    for (const [, refusal] of AUTH_REFUSALS) refuse(socket, withoutData(refusal));
    expect(vi.getTimerCount()).toBe(0);

    refuse(socket, withoutData(gateways.notification.sessionCheckUnavailable));
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MS);
    expect(socket.connect).toHaveBeenCalledTimes(1);
  });
});

describe('teardown', () => {
  it('cancels a pending retry and removes every listener on disconnect', async () => {
    mod.connectNotificationSocket('token-1');
    const socket = created[0];
    expect(socket.listenerCount()).toBeGreaterThan(0);
    refuse(socket);
    expect(vi.getTimerCount()).toBe(1);

    mod.disconnectNotificationSocket();

    expect(vi.getTimerCount()).toBe(0);
    expect(socket.disconnect).toHaveBeenCalled();
    expect(socket.listenerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MAX_MS);
    expect(socket.connect).not.toHaveBeenCalled();
  });

  it('a new token replaces the socket: the old retry is cancelled and the old socket is never revived', async () => {
    mod.connectNotificationSocket('token-1');
    const first = created[0];
    refuse(first);

    mod.connectNotificationSocket('token-2');
    const second = created[1];

    expect(second).toBeDefined();
    expect(second.options.auth).toEqual({ token: 'Bearer token-2' });
    expect(first.listenerCount()).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MAX_MS);
    expect(first.connect).not.toHaveBeenCalled();
    expect(second.connect).not.toHaveBeenCalled();

    // and the new socket retries on its own, starting from the first delay again
    refuse(second);
    await vi.advanceTimersByTimeAsync(mod.RECONNECT_DELAY_MS);
    expect(second.connect).toHaveBeenCalledTimes(1);
  });
});
