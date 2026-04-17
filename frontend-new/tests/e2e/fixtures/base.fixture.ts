import { test as base, expect } from '@playwright/test';

/**
 * Base test fixture that extends Playwright's default test.
 * All authenticated tests should import { test, expect } from here.
 */
export const test = base;
export { expect };
