import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  desktopAuthorizeParams,
  desktopIntegrationErrorMessage,
  runDesktopIntegrationOAuth,
} from '../desktop-oauth-return';
import { DesktopOAuthError } from '../oauth-deep-link';

const STATE = 'phd.eyJzdGF0ZSI6ICJ4In0=';
const AUTH_URL = `https://accounts.example.com/authorize?client_id=c&state=${encodeURIComponent(STATE)}`;

type Listener = (payload: unknown) => void;

function installBridge() {
  let listener: Listener = () => {};
  const bridge = {
    openExternal: vi.fn().mockResolvedValue({ ok: true }),
    consumePending: vi.fn().mockResolvedValue(null),
    onCallback: vi.fn((cb: Listener) => {
      listener = cb;
      return () => {
        listener = () => {};
      };
    }),
    exchangeToken: vi.fn(),
  };
  (window as unknown as { electronAPI?: unknown }).electronAPI = { isElectron: true, oauth: bridge };
  return {
    bridge,
    deliver: (payload: unknown) => listener(payload),
  };
}

function link(params: Record<string, string>, overrides: Record<string, unknown> = {}) {
  return { kind: 'integration', provider: 'connector', params, receivedAt: Date.now(), ...overrides };
}

describe('runDesktopIntegrationOAuth', () => {
  let deliver: (payload: unknown) => void;
  let bridge: ReturnType<typeof installBridge>['bridge'];

  beforeEach(() => {
    ({ deliver, bridge } = installBridge());
  });

  afterEach(() => {
    delete (window as unknown as { electronAPI?: unknown }).electronAPI;
  });

  it('refuses without opening the browser when the server did not mark the state', async () => {
    const complete = vi.fn();
    const flow = runDesktopIntegrationOAuth({
      family: 'connector',
      authUrl: 'https://accounts.example.com/authorize?state=eyJzdGF0ZSI6ICJ4In0%3D',
      complete,
    });
    await expect(flow.promise).rejects.toMatchObject({ reason: 'unavailable' });
    expect(bridge.openExternal).not.toHaveBeenCalled();
    expect(complete).not.toHaveBeenCalled();
  });

  it('completes the callback with the code and state from the matching deep link', async () => {
    const complete = vi.fn().mockResolvedValue(undefined);
    const flow = runDesktopIntegrationOAuth({ family: 'connector', authUrl: AUTH_URL, complete });
    await vi.waitFor(() => expect(bridge.openExternal).toHaveBeenCalledWith(AUTH_URL));

    deliver(link({ code: 'c0de', state: STATE }));

    await expect(flow.promise).resolves.toBeUndefined();
    expect(complete).toHaveBeenCalledWith({ code: 'c0de', state: STATE });
  });

  it('ignores links for another state, another family, or sign-in', async () => {
    const complete = vi.fn().mockResolvedValue(undefined);
    const flow = runDesktopIntegrationOAuth({ family: 'connector', authUrl: AUTH_URL, complete });

    deliver(link({ code: 'x', state: 'phd.other' }));
    deliver(link({ code: 'x', state: STATE }, { provider: 'toolset' }));
    deliver(link({ code: 'x', state: STATE }, { kind: 'auth', provider: 'google' }));
    expect(complete).not.toHaveBeenCalled();

    deliver(link({ code: 'real', state: STATE }));
    await flow.promise;
    expect(complete).toHaveBeenCalledTimes(1);
    expect(complete).toHaveBeenCalledWith({ code: 'real', state: STATE });
  });

  it('surfaces a provider error without completing', async () => {
    const complete = vi.fn();
    const flow = runDesktopIntegrationOAuth({ family: 'mcp', authUrl: AUTH_URL, complete });

    deliver(link({ error: 'access_denied', error_description: 'User cancelled', state: STATE }, { provider: 'mcp' }));

    await expect(flow.promise).rejects.toMatchObject({ reason: 'provider-error', message: 'User cancelled' });
    expect(complete).not.toHaveBeenCalled();
  });

  it('propagates a backend rejection from complete', async () => {
    const flow = runDesktopIntegrationOAuth({
      family: 'toolset',
      authUrl: AUTH_URL,
      complete: vi.fn().mockRejectedValue(new Error('Only the creator can authorize this connector')),
    });

    deliver(link({ code: 'c0de', state: STATE }, { provider: 'toolset' }));

    await expect(flow.promise).rejects.toThrow('Only the creator can authorize this connector');
  });

  it('picks up a link that arrived before the listener attached', async () => {
    bridge.consumePending.mockResolvedValue(link({ code: 'early', state: STATE }));
    const complete = vi.fn().mockResolvedValue(undefined);

    await runDesktopIntegrationOAuth({ family: 'connector', authUrl: AUTH_URL, complete }).promise;

    expect(complete).toHaveBeenCalledWith({ code: 'early', state: STATE });
  });

  it('treats cancel as silent', async () => {
    const flow = runDesktopIntegrationOAuth({ family: 'connector', authUrl: AUTH_URL, complete: vi.fn() });
    flow.cancel();
    const error = await flow.promise.catch((e: unknown) => e);
    expect(desktopIntegrationErrorMessage(error)).toBeNull();
  });
});

describe('desktopIntegrationErrorMessage', () => {
  it('explains timeouts and browser-open failures', () => {
    expect(desktopIntegrationErrorMessage(new DesktopOAuthError('timeout', 'x'))).toMatch(/timed out/);
    expect(desktopIntegrationErrorMessage(new DesktopOAuthError('open-failed', 'x'))).toMatch(/browser/);
  });
});

describe('desktopAuthorizeParams', () => {
  afterEach(() => {
    delete (window as unknown as { electronAPI?: unknown }).electronAPI;
  });

  it('asks for a desktop state only inside Electron', () => {
    expect(desktopAuthorizeParams()).toEqual({});
    (window as unknown as { electronAPI?: unknown }).electronAPI = { isElectron: true };
    expect(desktopAuthorizeParams()).toEqual({ desktop: 'true' });
  });
});
