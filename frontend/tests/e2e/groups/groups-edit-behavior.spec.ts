import { test, expect } from '../fixtures/api-context.fixture';
import type { APIRequestContext, Page } from '@playwright/test';
import { GroupType } from '@/app/(main)/workspace/groups/types';

interface ApiUserRecord {
  _id?: string;
  userId?: string;
  id?: string;
  email?: string;
  fullName?: string;
  name?: string;
}

async function openCreateGroupPanel(page: Page) {
  const ctaButton = page.locator('button').filter({ hasText: /Create/i }).first();
  await ctaButton.click();
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 10_000 });
}

async function searchGroup(page: Page, name: string) {
  const searchInput = page.locator('input[placeholder*="Search"]');
  await searchInput.fill(name);
  await page.waitForTimeout(800);
}

async function openGroupDetailByName(page: Page, namePattern: RegExp) {
  const row = page.locator('[role="row"]').filter({ hasText: namePattern }).first();
  await expect(row).toBeVisible({ timeout: 10_000 });
  await row.click();
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 10_000 });
}

async function createGroupViaApi(
  apiContext: APIRequestContext,
  name: string
) {
  const response = await apiContext.post('/api/v1/userGroups', {
    data: { name, type: GroupType.CUSTOM },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function getCandidateUser(
  apiContext: APIRequestContext
) {
  const response = await apiContext.get('/api/v1/users', {
    params: { page: 1, limit: 25 },
  });
  expect(response.ok()).toBeTruthy();
  const payload = (await response.json()) as { users?: ApiUserRecord[] };
  const users = Array.isArray(payload?.users) ? payload.users : [];
  const candidate = users.find((u) => u?.email) ?? users[0];
  expect(candidate).toBeTruthy();

  return {
    userId: String(candidate._id || candidate.userId || candidate.id),
    email: String(candidate.email || ''),
    name: String(candidate.fullName || candidate.name || candidate.email || ''),
  };
}

async function expectUserInGroup(
  apiContext: APIRequestContext,
  groupId: string,
  userId: string,
  shouldExist: boolean
) {
  await expect
    .poll(
      async () => {
        const response = await apiContext.get(`/api/v1/userGroups/${groupId}/users`, {
          params: { page: 1, limit: 100 },
        });
        if (!response.ok()) return false;
        const payload = (await response.json()) as {
          users?: Array<{ _id?: string; userId?: string; id?: string }>;
        };
        const users = payload.users ?? [];
        return users.some((u) => String(u._id || u.userId || u.id) === userId);
      },
      { timeout: 10_000, intervals: [500, 1_000, 1_500] }
    )
    .toBe(shouldExist);
}

test.describe('Groups Edit Behavior', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/groups/');
    await page.waitForTimeout(2_000);
  });

  test('custom group name can be renamed', async ({ page }) => {
    const createdName = `E2E Rename ${Date.now()}`;
    const renamedName = `${createdName} Updated`;

    await openCreateGroupPanel(page);

    const createDialog = page.getByRole('dialog');
    await createDialog.locator('input[placeholder="e.g. Data Engineering"]').fill(createdName);
    await createDialog.getByRole('button', { name: 'Create Group' }).click();
    await expect(createDialog).toBeHidden({ timeout: 15_000 });

    await searchGroup(page, createdName);
    await openGroupDetailByName(page, new RegExp(createdName, 'i'));

    const detailDialog = page.getByRole('dialog');
    await detailDialog.getByRole('button', { name: /Edit Group/i }).click();

    const nameInput = detailDialog.locator('input[type="text"]').first();
    await nameInput.fill(renamedName);
    await detailDialog.getByRole('button', { name: /Save Edits/i }).click();

    await expect(detailDialog.getByRole('button', { name: /Edit Group/i })).toBeVisible({
      timeout: 15_000,
    });
    await detailDialog.getByRole('button', { name: /Cancel/i }).click();
    await expect(detailDialog).toBeHidden({ timeout: 10_000 });

    await searchGroup(page, renamedName);
    await expect(page.locator('[role="row"]').filter({ hasText: new RegExp(renamedName, 'i') }).first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test('admin and everyone group names are locked with tooltip explanation', async ({ page }) => {
    for (const groupName of [GroupType.ADMIN, GroupType.EVERYONE]) {
      await page.goto('/workspace/groups/');
      await page.waitForTimeout(1_000);
      await searchGroup(page, groupName);
      await openGroupDetailByName(page, new RegExp(groupName, 'i'));

      const detailDialog = page.getByRole('dialog');
      await detailDialog.getByRole('button', { name: /Edit Group/i }).click();

      const nameInput = detailDialog.locator('input[type="text"]').first();
      await expect(nameInput).toHaveAttribute('readonly', '');

      await nameInput.hover();
      await expect(
        page.getByRole('tooltip').filter({ hasText: /system-defined and cannot be changed/i }).first()
      ).toBeVisible({ timeout: 5_000 });

      // First cancel exits edit mode; second cancel closes the panel.
      await detailDialog.getByRole('button', { name: /Cancel/i }).click();
      await expect(detailDialog.getByRole('button', { name: /Edit Group/i })).toBeVisible({
        timeout: 10_000,
      });
      await detailDialog.getByRole('button', { name: /Cancel/i }).click();
      await expect(detailDialog).toBeHidden({ timeout: 10_000 });
    }
  });

  test('can add user to a custom group from edit sidebar', async ({ page, apiContext }) => {
    const groupName = `E2E Add User ${Date.now()}`;
    const user = await getCandidateUser(apiContext);
    const group = await createGroupViaApi(apiContext, groupName);

    await page.goto('/workspace/groups/');
    await page.waitForTimeout(1_500);
    await searchGroup(page, groupName);
    await openGroupDetailByName(page, new RegExp(groupName, 'i'));

    const detailDialog = page.getByRole('dialog');
    await detailDialog.getByRole('button', { name: /Edit Group/i }).click();

    // Open add-users dropdown and select target user by email
    await detailDialog
      .locator('text=/Search or select user\\(s\\) to add to this group/i')
      .first()
      .click();
    const addUserSearch = detailDialog
      .locator('input[placeholder*="Search or select user(s) to add to this group"]')
      .first();
    await addUserSearch.fill(user.email || user.name);
    const option = page.getByText(user.email || user.name, { exact: false }).first();
    await expect(option).toBeVisible({ timeout: 10_000 });
    await option.click();
    await expect(detailDialog.getByText(/1 Selected/i)).toBeVisible({ timeout: 5_000 });

    await detailDialog.getByRole('button', { name: /Save Edits/i }).click();
    await expect(detailDialog.getByRole('button', { name: /Edit Group/i })).toBeVisible({
      timeout: 15_000,
    });
    await expectUserInGroup(apiContext, String(group._id), user.userId, true);
  });

  test('can remove user from a custom group from edit sidebar', async ({ page, apiContext }) => {
    const groupName = `E2E Remove User ${Date.now()}`;
    const user = await getCandidateUser(apiContext);
    const group = await createGroupViaApi(apiContext, groupName);

    const addResponse = await apiContext.post('/api/v1/userGroups/add-users', {
      data: { userIds: [user.userId], groupIds: [group._id] },
    });
    expect(addResponse.ok()).toBeTruthy();

    await page.goto('/workspace/groups/');
    await page.waitForTimeout(1_500);
    await searchGroup(page, groupName);
    await openGroupDetailByName(page, new RegExp(groupName, 'i'));

    const detailDialog = page.getByRole('dialog');
    await detailDialog.getByRole('button', { name: /Edit Group/i }).click();

    const userRow = detailDialog
      .locator('div')
      .filter({ hasText: new RegExp(user.email || user.name, 'i') })
      .first();
    await expect(userRow).toBeVisible({ timeout: 10_000 });
    await userRow.getByText('Remove', { exact: true }).click();

    await detailDialog.getByRole('button', { name: /Save Edits/i }).click();
    await expect(detailDialog.getByRole('button', { name: /Edit Group/i })).toBeVisible({
      timeout: 15_000,
    });
    await expectUserInGroup(apiContext, String(group._id), user.userId, false);
  });
});
