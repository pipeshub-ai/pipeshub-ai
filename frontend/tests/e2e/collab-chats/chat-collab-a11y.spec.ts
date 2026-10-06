import { test, expect } from './support/two-users.fixture';
import { composer, openChat } from './support/chat-ui';
import { expectNoBlockingViolations } from './support/a11y';

test.describe.configure({ mode: 'serial' });

const ACCESS_BUTTON = 'Who can access this chat';

test('UX-11: the access panel opens from the keyboard and has no serious violations (owner and editor)', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const chat = await a.api.startChat('A first question');
  expect((await a.api.share(chat, b.actor, 'write')).status).toBe(200);

  for (const [who, page, sentence] of [
    ['owner', a.page, 'You own this chat.'],
    ['editor', b.page, 'You can continue this chat because it was shared with you directly.'],
  ] as const) {
    await openChat(page, chat);
    const trigger = page.getByRole('button', { name: ACCESS_BUTTON });
    await trigger.focus();
    await page.keyboard.press('Enter');
    const panel = page.getByRole('dialog', { name: 'Access to this chat' });
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId('access-explain')).toContainText(sentence, { timeout: 15_000 });
    await expectNoBlockingViolations(page, `access-panel-${who}`);
    await page.keyboard.press('Escape');
    await expect(panel).toHaveCount(0);
    await expect(trigger).toBeFocused();
  }
});

test('UX-11: the access-change dialog lists who is affected, has no serious violations, and Cancel changes nothing', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const project = await a.api.call('POST', '/api/v1/projects', { name: 'A11y project' });
  expect(project.status, JSON.stringify(project.body)).toBeLessThan(300);
  const projectId: string = project.body.project?._id ?? project.body.project?.id;
  const chat = await a.api.startChat('A first question');
  expect((await a.api.call('PUT', `/api/v1/conversations/${chat}/project`, { projectId })).status).toBeLessThan(300);
  expect((await a.api.share(chat, b.actor, 'write')).status).toBe(200);

  await openChat(a.page, chat);
  await a.page.getByRole('button', { name: ACCESS_BUTTON }).click();
  const visibility = a.page.getByRole('switch', { name: 'Visible to project members' });
  await expect(visibility).toBeVisible({ timeout: 15_000 });
  const before = await visibility.getAttribute('aria-checked');
  await visibility.focus();
  await a.page.keyboard.press('Space');

  const dialog = a.page.getByRole('dialog', { name: 'Review access changes' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Checking who is affected…')).toHaveCount(0, { timeout: 15_000 });
  await expect(dialog.getByRole('button', { name: 'Apply' })).toBeVisible();
  await expectNoBlockingViolations(a.page, 'access-change-dialog');

  const visibilityWrites = [] as string[];
  a.page.on('request', (r) => {
    if (r.url().includes('/project-visibility')) visibilityWrites.push(r.method());
  });
  await a.page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  expect(visibilityWrites).toEqual([]);
  await a.page.getByRole('button', { name: ACCESS_BUTTON }).click();
  await expect(visibility).toHaveAttribute('aria-checked', before ?? 'false');
});

test('UX-11: the share drawer is keyboard-operable: pick a person, choose a level, share; Escape closes the menu first', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const chat = await a.api.startChat('A first question');

  await openChat(a.page, chat);
  await expect(composer(a.page)).toBeVisible();
  const share = a.page.getByRole('button', { name: 'Share', exact: true }).first();
  await share.focus();
  await a.page.keyboard.press('Enter');
  const drawer = a.page.getByRole('dialog', { name: 'Share chat' });
  await expect(drawer).toBeVisible();

  await drawer.getByPlaceholder(/Emails, teams or names/).fill(b.actor.email);
  await drawer.getByRole('checkbox', { name: 'User Writer' }).focus();
  await a.page.keyboard.press('Space');
  const level = drawer.getByRole('button', { name: /Can view/ });
  await level.focus();
  await a.page.keyboard.press('Enter');
  await expect(drawer.getByRole('menuitemradio', { name: /Can view/ })).toBeFocused();
  await expectNoBlockingViolations(a.page, 'share-drawer-role-menu');

  // Escape closes the menu, not the drawer, and focus goes back to the level button.
  await a.page.keyboard.press('Escape');
  await expect(drawer.getByRole('menu')).toHaveCount(0);
  await expect(drawer).toBeVisible();
  await expect(level).toBeFocused();

  await a.page.keyboard.press('Enter');
  // The menu takes focus once it has been placed (next frame).
  await expect(drawer.getByRole('menuitemradio', { name: /Can view/ })).toBeFocused();
  await a.page.keyboard.press('ArrowDown');
  await expect(drawer.getByRole('menuitemradio', { name: /Can continue/ })).toBeFocused();
  await a.page.keyboard.press('Enter');
  const submit = drawer.getByRole('button', { name: 'Share', exact: true });
  await expect(drawer.getByRole('button', { name: /Can continue/ })).toBeVisible();
  const shared = a.page.waitForResponse((r) => r.url().includes('/collaborators') && r.request().method() === 'PUT');
  await submit.focus();
  await a.page.keyboard.press('Enter');
  const response = await shared;
  expect(response.status()).toBe(200);
  expect(JSON.parse(response.request().postData() ?? '{}').collaborators).toEqual([
    { principalType: 'user', principalId: b.actor.userId, accessLevel: 'write' },
  ]);
});
