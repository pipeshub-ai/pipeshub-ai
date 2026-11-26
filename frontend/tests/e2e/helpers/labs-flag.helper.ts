import { request, expect, type APIRequestContext } from '@playwright/test';
import { apiBaseURL, getAccessToken } from '../fixtures/api-context.fixture';

const SETTINGS = '/api/v1/configurationManager/platform/settings';

type Settings = { featureFlags?: Record<string, boolean>; fileUploadMaxSizeBytes?: number; agentHarness?: unknown };

/** The seeded admin's API, outside any test's fixtures (for beforeAll/afterAll). */
export const adminApi = (): Promise<APIRequestContext> =>
  request.newContext({ baseURL: apiBaseURL(), extraHTTPHeaders: { Authorization: `Bearer ${getAccessToken()}` } });

/**
 * Whether the harness is on for the signed-in org (ADR-OPS-08): the server switch, its infrastructure and the org's
 * Labs switch. A spec of a harness page skips where it is off (run.sh off, or Labs off), as the page is not there.
 */
export async function harnessSwitchedOn(api: APIRequestContext): Promise<boolean> {
  const res = await api.get('/api/v1/configurationManager/platform/feature-flags/effective');
  expect(res.ok(), 'the effective flags answer').toBe(true);
  return ((await res.json()) as { agentHarness?: { harnessOn?: boolean } | null }).agentHarness?.harnessOn === true;
}

/**
 * Sets one Labs flag by read-merge-write on the platform settings and returns
 * its earlier value. Specs in other workers write the same document, so a
 * write can drop this one's flag: it is read back and written again until it
 * holds.
 */
export async function setLabsFlag(api: APIRequestContext, flag: string, on: boolean): Promise<boolean> {
  let before: boolean | null = null;
  await expect
    .poll(
      async () => {
        const current = (await (await api.get(SETTINGS)).json()) as Settings;
        if (before === null) before = current.featureFlags?.[flag] === true;
        if (current.featureFlags?.[flag] === on) return true;
        const body: Settings = {
          featureFlags: { ...(current.featureFlags ?? {}), [flag]: on },
          fileUploadMaxSizeBytes: current.fileUploadMaxSizeBytes || 30 * 1024 * 1024,
          ...(current.agentHarness != null ? { agentHarness: current.agentHarness } : {}),
        };
        expect((await api.post(SETTINGS, { data: body })).ok(), 'the platform settings accept the flag').toBe(true);
        return false;
      },
      { timeout: 30_000, intervals: [500, 1_000] },
    )
    .toBe(true);
  return before === true;
}
