/**
 * Sharing a collection with a teammate, and taking it back, seen from both sides.
 *
 * The admin shares through the Share dialog; a real second user, invited and
 * signed in through their own browser session, must then see the collection
 * and its file. After the admin removes their access, the same user must no
 * longer see it. Every other suite runs as the admin, who can see everything,
 * so none of them can tell "access enforced" from "access ignored".
 */
import { test, expect } from '../fixtures/api-context.fixture';
import type { Page } from '@playwright/test';
import { ensureSmtpConfigured } from '../helpers/smtp.helper';
import {
  acceptInvite,
  deleteUserByEmail,
  inviteByApi,
  signInAs,
  uniqueMemberEmail,
  type Member,
} from '../helpers/members.helper';
import { createTestKb, deleteTestKb, makeFiles, makeKbName, openUploadSidebar } from './kb-upload.helpers';

const SEARCH_PLACEHOLDER = 'Emails, teams or names (separated by commas)';

async function openShareDialog(page: Page, kbId: string) {
  await page.goto(`/knowledge-base/?nodeType=app&nodeId=${kbId}`);
  // exact: the sidebar lists the collection itself, whose name may contain "Share".
  await page.getByRole('button', { name: 'Share', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Share Collection' });
  await expect(dialog).toBeVisible({ timeout: 15_000 });
  return dialog;
}

/**
 * Open the collections page as `page`'s user and wait until their own
 * collections have rendered, so "not listed" is checked against a loaded list.
 */
async function openCollections(page: Page) {
  await page.goto('/knowledge-base/');
  await expect(page.getByRole('button', { name: /'s Private$/ }).first()).toBeVisible({ timeout: 30_000 });
}

/**
 * Open a collection by its link and return the file-list response the page
 * loaded for it. The sidebar asks the same URL with `onlyContainers=true`,
 * which leaves files out even when the user has access, so only the data
 * area's own request (it asks for breadcrumbs) says what the user can see.
 */
async function openCollectionContents(page: Page, kbId: string) {
  const dataArea = page.waitForResponse((r) => {
    const url = new URL(r.url());
    return (
      r.request().method() === 'GET' &&
      url.pathname.endsWith(`/knowledge-hub/nodes/app/${kbId}`) &&
      url.searchParams.get('onlyContainers') !== 'true' &&
      (url.searchParams.get('include') ?? '').includes('breadcrumbs')
    );
  });
  await page.goto(`/knowledge-base/?nodeType=app&nodeId=${kbId}`);
  const response = await dataArea;
  const items = response.ok()
    ? (((await response.json()) as { items?: Array<{ name?: string }> }).items ?? [])
    : [];
  return { status: response.status(), names: items.map((i) => i.name ?? '') };
}

function collectionEntry(page: Page, kbName: string) {
  // A collection with files gets an expand chevron whose icon text leads the accessible name.
  const escaped = kbName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return page.getByRole('button', { name: new RegExp(`(^|\\s)${escaped}$`) });
}

test.describe('Share a collection', () => {
  test.describe.configure({ mode: 'serial' });

  let member: Member | undefined;
  // Kept apart from `member` so the invited user is removed even if accepting fails.
  let memberEmail: string | undefined;
  let kb: { id: string; name: string } | undefined;

  test.beforeAll(async ({ apiContext, browser }) => {
    test.skip(
      !(await ensureSmtpConfigured(apiContext)),
      'SMTP is not configured and SMTP_HOST / SMTP_PORT are not set; a second user can only join by invite',
    );
    test.setTimeout(180_000);
    memberEmail = uniqueMemberEmail('share');
    await inviteByApi(apiContext, memberEmail);
    member = await acceptInvite(browser, memberEmail);
    kb = await createTestKb(apiContext, makeKbName('share'));
  });

  test.afterAll(async ({ apiContext }) => {
    try {
      if (kb) await deleteTestKb(apiContext, kb.id);
    } finally {
      if (memberEmail) await deleteUserByEmail(apiContext, memberEmail);
    }
  });

  test('a teammate sees a shared collection and its file, and loses it when access is removed', async ({
    page,
    browser,
  }) => {
    test.setTimeout(240_000);
    const { id: kbId, name: kbName } = kb!;
    const { email } = member!;

    const input = await openUploadSidebar(page, kbId);
    expect(input, 'Upload Data entry point should be available').not.toBeNull();
    const [file] = makeFiles(1, { prefix: `shared-${Date.now()}` });
    // Listings show the name without its extension.
    const fileLabel = file.name.replace(/\.pdf$/, '');
    await input!.setInputFiles([file]);
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(
      page.locator('[data-testid="upload-item-row"][data-status="completed"]'),
    ).toHaveCount(1, { timeout: 60_000 });

    const memberPage = await signInAs(browser, member!);
    try {
      // Not shared yet: the teammate must not see it.
      await openCollections(memberPage);
      await expect(collectionEntry(memberPage, kbName)).toHaveCount(0);

      let dialog = await openShareDialog(page, kbId);
      const search = dialog.getByPlaceholder(SEARCH_PLACEHOLDER);
      await search.fill(email);
      // Pick the suggested match, as a person would; the dialog loads its user list in the background.
      await dialog.getByText(email).first().click();
      await dialog.getByRole('button', { name: 'Share', exact: true }).click();
      await expect(page.getByText('Access shared', { exact: true })).toBeVisible({ timeout: 15_000 });

      await openCollections(memberPage);
      const sharedEntry = collectionEntry(memberPage, kbName);
      await expect(sharedEntry, 'the shared collection should appear for the teammate').toBeVisible({
        timeout: 30_000,
      });
      await sharedEntry.click();
      await expect(memberPage.getByText(fileLabel).first(), 'the teammate should see the file inside').toBeVisible({
        timeout: 30_000,
      });
      // The same request the revoked check below relies on must list the file while access lasts.
      const before = await openCollectionContents(memberPage, kbId);
      expect(before.names, 'the file list should include the shared file').toContain(fileLabel);
      await expect(memberPage.getByText(fileLabel).first()).toBeVisible({ timeout: 30_000 });

      dialog = await openShareDialog(page, kbId);
      // After sharing, the member's row is the only one with a role menu; the owner has none.
      await dialog.getByRole('button', { name: /Can view/ }).click();
      await page.getByText('Remove', { exact: true }).click();
      await expect(page.getByText('Access revoked', { exact: true })).toBeVisible({ timeout: 15_000 });

      await openCollections(memberPage);
      await expect(
        collectionEntry(memberPage, kbName),
        'the collection should disappear once access is removed',
      ).toHaveCount(0);

      // Even with the old link, its files must not be listed: the server refuses or returns nothing.
      const after = await openCollectionContents(memberPage, kbId);
      if (after.status < 400) {
        expect(after.names, 'the server should return none of the collection\'s files').toEqual([]);
        // The table has finished loading once it shows its empty state; only then is absence meaningful.
        await expect(memberPage.getByText(`${kbName} is empty`)).toBeVisible({ timeout: 30_000 });
      } else {
        expect([403, 404], 'a refused request should be 403 or 404').toContain(after.status);
      }
      await expect(memberPage.getByText(fileLabel)).toHaveCount(0);
    } finally {
      await memberPage.context().close();
    }
  });
});
