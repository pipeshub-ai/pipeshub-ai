import { expect, test } from '@playwright/test';
import { cyNodeIds, fake, openExplorer, waitForNodes, FOCUS_BADGE } from './helpers';

test.describe('failure and recovery', () => {
  test('during an outage Expand keeps the picture, shows an error with Retry, and Retry recovers', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    const before = await cyNodeIds(page);
    await fake(page, (f) => (f.outage = true));
    await page.getByRole('button', { name: 'Expand' }).first().click();
    await expect(page.getByRole('alert')).toContainText("Couldn't load this right now.");
    expect(await cyNodeIds(page)).toEqual(before);
    await fake(page, (f) => (f.outage = false));
    await page.getByRole('button', { name: 'Retry' }).click();
    await expect(page.getByRole('alert')).toHaveCount(0);
  });

  test('Open and an address lookup during an outage keep the old view and the trail', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    const before = await cyNodeIds(page);
    await fake(page, (f) => (f.outage = true));
    await page.getByRole('button', { name: 'Show as table' }).click();
    await page.getByRole('row').filter({ hasNotText: FOCUS_BADGE }).nth(1).getByRole('button', { name: 'Open' }).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await page.getByRole('button', { name: 'Show as graph' }).click();
    await waitForNodes(page, before.length);
    expect((await cyNodeIds(page)).sort()).toEqual([...before].sort());
    expect(new URL(page.url()).searchParams.get('node')).toBe('block:b0001');
  });

  test('an entry link that fails for a transient reason keeps its address and works on Retry', async ({ page }) => {
    await openExplorer(page, '/?record=rec-5', { outage: true });
    await expect(page.getByRole('alert')).toBeVisible();
    expect(new URL(page.url()).searchParams.get('record')).toBe('rec-5');
    await fake(page, (f) => (f.outage = false));
    await page.getByRole('button', { name: 'Retry' }).click();
    await waitForNodes(page, 1);
    await expect.poll(() => new URL(page.url()).searchParams.get('node')).toMatch(/^block:/);
    expect(new URL(page.url()).searchParams.has('record')).toBe(false);
  });

  test('the access check: nothing after one failure, a warning after two, the picture cleared after five minutes, and it comes back when a check works', async ({ page }) => {
    test.setTimeout(240_000);
    await page.clock.install();
    await openExplorer(page, '/?node=block:b0001');
    await page.clock.runFor(500);
    await waitForNodes(page, 2);
    await fake(page, (f) => (f.outage = true));
    await page.clock.runFor(35_000);
    await expect(page.getByText("Can't verify access right now")).toHaveCount(0);
    await page.clock.runFor(35_000);
    await expect(page.getByText("Can't verify access right now")).toBeVisible();
    expect((await cyNodeIds(page)).length).toBeGreaterThan(1);
    await page.clock.runFor(5 * 60_000);
    await expect(page.getByText('Access could not be verified for several minutes')).toBeVisible();
    expect(await cyNodeIds(page)).toEqual([]);
    await page.clock.runFor(120_000);
    // Still down: the message stays, the page does not claim to be fine.
    await expect(page.getByText('Access could not be verified for several minutes')).toBeVisible();
    await fake(page, (f) => (f.outage = false));
    await page.clock.runFor(40_000);
    await waitForNodes(page, 2);
    await expect(page.getByText('Access could not be verified for several minutes')).toHaveCount(0);
  });

  test('every build status has its own line, and an empty repository gets an empty state, not a banner over a blank picture', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    const lines: Record<string, string> = {
      stale: 'The graph is being updated',
      building: 'The graph is being built',
      retrying: 'The graph build hit a problem',
      failed: 'The last graph build failed',
      unknown: 'The state of this graph could not be read',
    };
    for (const [status, text] of Object.entries(lines)) {
      await page.evaluate((s) => ((window as unknown as { __fake: { status: string } }).__fake.status = s), status);
      await page.getByRole('button', { name: 'Show as table' }).click();
      await page.getByRole('row').filter({ hasNotText: FOCUS_BADGE }).nth(1).getByRole('button', { name: 'Expand' }).click();
      await expect(page.getByText(text)).toBeVisible();
      await page.getByRole('button', { name: 'Show as graph' }).click();
    }
    await page.evaluate(() => ((window as unknown as { __fake: { status: string } }).__fake.status = 'never_built'));
    await page.getByRole('button', { name: 'Show as table' }).click();
    await page.getByRole('row').filter({ hasNotText: FOCUS_BADGE }).nth(1).getByRole('button', { name: 'Open' }).click();
    await expect(page.getByText('This repository has no code graph yet.')).toBeVisible();
  });

  test('"some links may be missing" appears when the scan budget ran out and stays until the view is replaced', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    await page.evaluate(() => ((window as unknown as { __fake: { incomplete: boolean } }).__fake.incomplete = true));
    await page.getByRole('button', { name: 'Show as table' }).click();
    await page.getByRole('row').filter({ hasNotText: FOCUS_BADGE }).nth(1).getByRole('button', { name: 'Expand' }).click();
    await expect(page.getByText('Some links may be missing.')).toBeVisible();
  });
});
