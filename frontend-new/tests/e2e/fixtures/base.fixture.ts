import { test as base, expect } from '@playwright/test';
import { addCoverageReport } from 'monocart-reporter';

const COVERAGE_ENABLED = process.env.COVERAGE === 'true';

type CoverageFixtures = {
  autoCollectCoverage: void;
};

export const test = base.extend<CoverageFixtures>({
  autoCollectCoverage: [
    async ({ page }, use, testInfo) => {
      if (COVERAGE_ENABLED) {
        await page.coverage.startJSCoverage({ resetOnNavigation: false });
      }

      await use(undefined);

      if (COVERAGE_ENABLED) {
        const coverage = await page.coverage.stopJSCoverage();
        await addCoverageReport(coverage, testInfo);
      }
    },
    { auto: true },
  ],
});

export { expect };
