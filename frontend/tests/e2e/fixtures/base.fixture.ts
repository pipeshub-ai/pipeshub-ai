import { test as base, expect, type Request } from '@playwright/test';
import { addCoverageReport } from 'monocart-reporter';
import { pinScriptedModel } from '../harness/support/scripted-model';
import { refreshedState, signInWorkerSession, type StorageState } from '../helpers/session.helper';
import { isSharedSignIn, WorkerSession } from '../support/worker-session';

const COVERAGE_ENABLED = process.env.COVERAGE === 'true';

type CoverageFixtures = {
  autoCollectCoverage: void;
  reportRequestsWithoutResponse: void;
  failOnCspViolation: void;
};

/** A spec that provokes a CSP refusal on purpose (to prove one) says so with this annotation. */
export const EXPECTS_CSP_VIOLATION = { type: 'expects-csp-violation' } as const;
const CSP_VIOLATION = /Content Security Policy|Refused to (load|execute|connect|frame|apply|create)/i;

type WorkerFixtures = {
  workerSession: WorkerSession<StorageState>;
};

export const test = base.extend<CoverageFixtures, WorkerFixtures>({
  // R46-LANE (ADR-WEB-02 D9): a refresh replaces the session cookie, so the setup's one shared state goes stale; a
  // signed-in project's test runs on its worker's own sign-in instead (support/worker-session.ts).
  // A signed-in project's worker signs in before its first test, under its own timeout: with many workers the
  // account's sign-in limit (10 a minute by default) answers 429 and the sign-in waits as told.
  workerSession: [
    async ({}, use, workerInfo) => {
      const session = new WorkerSession<StorageState>(signInWorkerSession);
      if (isSharedSignIn(workerInfo.project.use.storageState)) await session.lend();
      await use(session);
    },
    { scope: 'worker', timeout: 200_000 },
  ],
  storageState: async ({ storageState, workerSession }, use) => {
    await use(isSharedSignIn(storageState) ? await workerSession.lend() : storageState);
  },
  // The harness lane: every page's chat turns go to the scripted model, whether or not a spec thinks of it, so no
  // spec can reach a stack's own (possibly paid) default model. `setup/harness-model.guard.ts` refuses a stack
  // where that would not hold.
  context: async ({ context, storageState, workerSession, baseURL }, use) => {
    if (process.env.E2E_HARNESS_LANE === '1') await pinScriptedModel(context);
    await use(context);
    if (workerSession.owns(storageState) && baseURL) {
      workerSession.settle(await refreshedState(context, new URL(baseURL).origin));
    }
  },
  autoCollectCoverage: [
    async ({ page, browserName }, use, testInfo) => {
      const canCollect = COVERAGE_ENABLED && browserName === 'chromium';

      if (canCollect) {
        await page.coverage.startJSCoverage({ resetOnNavigation: false });
      }

      await use(undefined);

      if (canCollect) {
        const coverage = await page.coverage.stopJSCoverage();
        await addCoverageReport(coverage, testInfo);
      }
    },
    { auto: true },
  ],
  // The app shows "Network error" for a request that got no response at all; a failed test names those
  // requests (method, path, the browser's reason), so a toast over the page can be traced to its request.
  reportRequestsWithoutResponse: [
    async ({ page }, use, testInfo) => {
      const failed: string[] = [];
      const onFailed = (request: Request) => {
        failed.push(`${request.method()} ${new URL(request.url()).pathname} ${request.failure()?.errorText ?? ''}`);
      };
      page.on('requestfailed', onFailed);
      await use(undefined);
      page.off('requestfailed', onFailed);
      if (failed.length > 0 && testInfo.status !== testInfo.expectedStatus) {
        await testInfo.attach('requests-without-response', { body: failed.join('\n'), contentType: 'text/plain' });
        console.log(`[${testInfo.title}] requests without a response:\n  ${failed.join('\n  ')}`);
      }
    },
    { auto: true },
  ],
  // SEC-D-07 (ADR-WEB-01): the CSP is enforced, so a refusal is a page that does not work for someone.
  failOnCspViolation: [
    async ({ page }, use, testInfo) => {
      const violations: string[] = [];
      const onConsole = (message: { text(): string }) => {
        const text = message.text();
        if (CSP_VIOLATION.test(text)) violations.push(text.slice(0, 400));
      };
      page.on('console', onConsole);
      await use(undefined);
      page.off('console', onConsole);
      if (testInfo.annotations.some((a) => a.type === EXPECTS_CSP_VIOLATION.type)) return;
      expect(violations, 'CSP violations (ADR-WEB-01): a page broke under the CSP').toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
