/**
 * The edition seam on the refresh call (ADR-WEB-02): an edition whose web app keeps its own session names the
 * credential the shared refresh sends, ahead of the cookie and the desktop keychain; unset, nothing changes.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { jsonResponse } from '@/lib/api/__tests__/sse-response';

const electron = { on: false };
vi.mock('@/lib/electron', () => ({
  isElectron: () => electron.on,
  applyElectronOverrides: (c: unknown) => c,
  getElectronApiBaseUrl: () => 'http://server.test',
}));

const { installEditionRefreshCredential, requestAccessToken } = await import('@/lib/auth/session-credential');

const fetchMock = vi.fn<typeof fetch>();
const sent = (i = 0) => fetchMock.mock.calls[i]![1] as RequestInit & { headers: Record<string, string> };

describe('edition refresh credential', () => {
  afterEach(() => {
    installEditionRefreshCredential(null);
    electron.on = false;
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("sends the edition's headers instead of the cookie", async () => {
    vi.stubGlobal('fetch', fetchMock.mockResolvedValue(jsonResponse(200, { accessToken: 'new-access' })));
    installEditionRefreshCredential(() => ({ Authorization: 'Bearer workspace-refresh' }));

    expect(await requestAccessToken()).toEqual({ outcome: 'issued', accessToken: 'new-access' });
    expect(sent().headers.Authorization).toBe('Bearer workspace-refresh');
  });

  it("an edition that names its org's cookie is answered from it and needs no keeper", async () => {
    vi.stubGlobal('fetch', fetchMock.mockResolvedValue(jsonResponse(200, { accessToken: 'new-access' })));
    installEditionRefreshCredential(() => ({ 'x-pipeshub-session': 'cookie', 'x-pipeshub-session-org': 'org-a' }));

    expect(await requestAccessToken()).toEqual({ outcome: 'issued', accessToken: 'new-access' });
    expect(sent().credentials).toBe('include');
    expect(sent().headers['x-pipeshub-session-org']).toBe('org-a');
    expect(sent().headers.Authorization).toBeUndefined();
  });

  it('wins over the desktop keychain', async () => {
    electron.on = true;
    const readRefreshToken = vi.fn(async () => 'keychain-refresh');
    vi.stubGlobal('window', { ...window, electronAPI: { session: { readRefreshToken } } });
    vi.stubGlobal('fetch', fetchMock.mockResolvedValue(jsonResponse(200, { accessToken: 'new-access' })));
    installEditionRefreshCredential(() => ({ Authorization: 'Bearer workspace-refresh' }));

    await requestAccessToken();
    expect(readRefreshToken).not.toHaveBeenCalled();
    expect(sent().headers.Authorization).toBe('Bearer workspace-refresh');
  });

  it('is refused without a request when the edition holds no session', async () => {
    vi.stubGlobal('fetch', fetchMock);
    installEditionRefreshCredential(() => null);

    expect(await requestAccessToken()).toEqual({ outcome: 'refused' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('unset, the web app sends its cookie', async () => {
    vi.stubGlobal('fetch', fetchMock.mockResolvedValue(jsonResponse(200, { accessToken: 'new-access' })));

    await requestAccessToken();
    expect(sent().credentials).toBe('include');
    expect(sent().headers.Authorization).toBeUndefined();
  });

  // R43AUTH-FU-1 (ADR-WEB-02 D9): the server replaces the refresh token on every refresh; the edition keeps it.
  it('hands the edition the replacement refresh token, and sends it on the next refresh', async () => {
    let stored = 'workspace-refresh-1';
    const keep = vi.fn(async (token: string) => {
      stored = token;
    });
    vi.stubGlobal(
      'fetch',
      fetchMock
        .mockResolvedValueOnce(jsonResponse(200, { accessToken: 'a1', refreshToken: 'workspace-refresh-2' }))
        .mockResolvedValueOnce(jsonResponse(200, { accessToken: 'a2', refreshToken: 'workspace-refresh-3' })),
    );
    installEditionRefreshCredential(() => ({ Authorization: `Bearer ${stored}` }), keep);

    expect(await requestAccessToken()).toMatchObject({ outcome: 'issued', accessToken: 'a1' });
    expect(keep).toHaveBeenCalledWith('workspace-refresh-2');
    await requestAccessToken();
    expect(sent(1).headers.Authorization).toBe('Bearer workspace-refresh-2');
    expect(stored).toBe('workspace-refresh-3');
  });

  it('two refreshes at once read the credential one after the other, after the first kept its replacement', async () => {
    const tails: Promise<unknown>[] = [];
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: {
        request: (_n: string, _o: unknown, work: () => Promise<unknown>) => {
          const run = (tails.at(-1) ?? Promise.resolve()).then(work);
          tails.push(run.catch(() => undefined));
          return run;
        },
      },
    });
    let stored = 't0';
    let n = 0;
    vi.stubGlobal(
      'fetch',
      fetchMock.mockImplementation(async () => {
        n += 1;
        return jsonResponse(200, { accessToken: `a${n}`, refreshToken: `t${n}` });
      }),
    );
    installEditionRefreshCredential(() => ({ Authorization: `Bearer ${stored}` }), (t) => {
      stored = t;
    });
    await Promise.all([requestAccessToken(), requestAccessToken()]);
    expect([sent(0).headers.Authorization, sent(1).headers.Authorization]).toEqual(['Bearer t0', 'Bearer t1']);
    delete (navigator as unknown as { locks?: unknown }).locks;
  });

  it('an edition may answer its credential later (a desktop edition reading its keyed vault); a failed read sends nothing', async () => {
    vi.stubGlobal('fetch', fetchMock.mockResolvedValue(jsonResponse(200, { accessToken: 'new-access' })));
    installEditionRefreshCredential(async () => ({ Authorization: 'Bearer from-the-vault' }));
    expect(await requestAccessToken()).toEqual({ outcome: 'issued', accessToken: 'new-access' });
    expect(sent().headers.Authorization).toBe('Bearer from-the-vault');

    fetchMock.mockClear();
    installEditionRefreshCredential(async () => {
      throw new Error('the keychain is locked');
    });
    expect(await requestAccessToken()).toEqual({ outcome: 'unreachable' });
    installEditionRefreshCredential(async () => null);
    expect(await requestAccessToken()).toEqual({ outcome: 'refused' });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
