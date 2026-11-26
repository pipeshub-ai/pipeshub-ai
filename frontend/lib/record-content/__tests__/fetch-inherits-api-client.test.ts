/**
 * The cache's transport is `apiClient`, so what every apiClient call gets, this one gets: the Electron
 * base URL, the refresh before an expired token is sent, one retry after a 401 and a refresh, the
 * sign-out on a finished session, the toast rules. The network is faked at the axios adapter and at
 * `fetch` (the refresh call).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { installMemoryStorage, jsonResponse, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';

const storage = installMemoryStorage();
installMemoryStorage('sessionStorage');

const toast = vi.hoisted(() => ({ showErrorToast: vi.fn() }));
vi.mock('@/lib/api/error-toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/error-toast')>()),
  showErrorToast: toast.showErrorToast,
}));
const logoutAndRedirect = vi.hoisted(() => vi.fn());
vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return {
    useAuthStore: auth.useAuthStore,
    logoutAndRedirect: () => {
      logoutAndRedirect();
      auth.useAuthStore.getState().logout();
    },
  };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { markSignedIn } = await import('@/lib/auth/session-credential');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');
const { fetchRecordContent } = await import('../fetch');
const { setSessionApiBaseUrl } = await import('@/lib/electron');

const STREAM = '/api/v1/knowledgeBase/stream/record/r1';
const fetchMock = vi.fn<typeof fetch>();
const args = (over: Partial<Parameters<typeof fetchRecordContent>[0]> = {}) => ({
  recordId: 'r1',
  variant: 'original' as const,
  ifNoneMatch: null,
  signal: new AbortController().signal,
  onProgress: vi.fn(),
  ...over,
});
const blob = () => new Blob(['x'], { type: 'application/pdf' });
const flush = () => new Promise((r) => setTimeout(r, 10));

beforeEach(() => {
  storage.clear();
  fetchMock.mockReset();
  logoutAndRedirect.mockReset();
  toast.showErrorToast.mockClear();
  vi.stubGlobal('fetch', fetchMock);
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'log').mockImplementation(() => {});
  const accessToken = jwtExpiringIn(3600, { orgId: 'o1', userId: 'u1' });
  // The refresh token is the session's HttpOnly cookie (ADR-WEB-02); the page keeps only who is signed in.
  markSignedIn(accessToken);
  useAuthStore.setState({ accessToken, isAuthenticated: true });
});

afterEach(() => {
  delete (window as unknown as { electronAPI?: unknown }).electronAPI;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the cache transport inherits apiClient', () => {
  it('(a) goes to the Electron API base URL, without cookies', async () => {
    (window as unknown as { electronAPI: unknown }).electronAPI = { isElectron: true };
    setSessionApiBaseUrl('http://api.example.test:3000');
    const api = fakeApi({ [`GET ${STREAM}`]: { status: 200, data: blob() } });
    await fetchRecordContent(args());
    expect(api.sent[0].config.baseURL).toBe('http://api.example.test:3000');
    expect(api.sent[0].config.withCredentials).toBe(false);
  });

  it('(c) refreshes an expired token before sending, and joins a refresh already in flight', async () => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(20, { orgId: 'o1', userId: 'u1' }) });
    const fresh = jwtExpiringIn(3600, { orgId: 'o1', userId: 'u1' });
    let answer!: (r: Response) => void;
    fetchMock.mockImplementation(() => new Promise<Response>((r) => (answer = r)));
    const api = fakeApi({ [`GET ${STREAM}`]: { status: 200, data: blob() } });
    const first = fetchRecordContent(args());
    const second = fetchRecordContent(args());
    await flush();
    expect(api.sent).toHaveLength(0); // nothing goes out with the expired token
    answer(jsonResponse(200, { accessToken: fresh }));
    await Promise.all([first, second]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(api.sent).toHaveLength(2);
    for (const s of api.sent) expect(s.config.headers.Authorization).toBe(`Bearer ${fresh}`);
  });

  it('(d) a 401 is refreshed and retried once, and a second 401 is not retried again', async () => {
    const fresh = jwtExpiringIn(3600, { orgId: 'o1', userId: 'u1' });
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(200, { accessToken: fresh })));
    const api = fakeApi({ [`GET ${STREAM}`]: [{ status: 401, data: {} }, { status: 200, data: blob() }] });
    await expect(fetchRecordContent(args())).resolves.toMatchObject({ status: 200 });
    expect(api.count(`GET ${STREAM}`)).toBe(2);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(api.sent[1].config.headers.Authorization).toBe(`Bearer ${fresh}`);

    const again = fakeApi({ [`GET ${STREAM}`]: { status: 401, data: {} } });
    await expect(fetchRecordContent(args())).rejects.toBeTruthy();
    expect(again.count(`GET ${STREAM}`)).toBe(2); // one retry, not a loop
  });

  it('(d) a 401 saying the session is over signs out without refreshing, and never settles', async () => {
    fakeApi({ [`GET ${STREAM}`]: { status: 401, data: { error: { message: 'Session expired, please login again' } } } });
    const settled = vi.fn();
    void fetchRecordContent(args()).then(settled, settled);
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
    expect(settled).not.toHaveBeenCalled();
  });

  it('(g) suppressErrorToast is honoured, and without it a refusal shows its toast', async () => {
    fakeApi({ [`GET ${STREAM}`]: { status: 403, data: { message: 'no' } } });
    await expect(fetchRecordContent(args({ suppressErrorToast: true }))).rejects.toMatchObject({ statusCode: 403 });
    expect(toast.showErrorToast).not.toHaveBeenCalled();
    await expect(fetchRecordContent(args())).rejects.toMatchObject({ statusCode: 403 });
    expect(toast.showErrorToast).toHaveBeenCalledTimes(1);
  });

  it('(h) lib/record-content has no raw fetch( call', () => {
    const walk = (dir: string, out: string[] = []): string[] => {
      for (const name of readdirSync(dir)) {
        const full = join(dir, name);
        if (statSync(full).isDirectory()) {
          if (name !== '__tests__') walk(full, out);
        } else if (/\.tsx?$/.test(name)) out.push(full);
      }
      return out;
    };
    const offenders = walk(join(__dirname, '..')).filter((f) => /(^|[^.\w])fetch\(/.test(readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '')));
    expect(offenders).toEqual([]);
  });
});
