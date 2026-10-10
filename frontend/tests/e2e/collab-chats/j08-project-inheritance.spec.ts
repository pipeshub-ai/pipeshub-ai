import { test, expect } from './support/two-users.fixture';
import { pdpAllows } from './support/stack';
import { composer, openChat } from './support/chat-ui';
import { expectNoBlockingViolations } from './support/a11y';
import { chatTitle } from './support/collab.helper';

/**
 * J-08: project inheritance. Everything runs through the real Node API and the access panel of the real UI.
 * Not on this lane: Python's record routes asking the PDP (a project viewer's 404 on an unconsented file), which are
 * `tests/unit/modules/authz/` and `tests/unit/connectors/api/test_router_chat_content_access.py`; the file half here is
 * Node's PDP endpoint called the way Python calls it.
 */
test.describe.configure({ mode: 'serial' });

const LOST = 'You no longer have access to this chat. What you see here is kept until you leave it.';
const READ_ONLY = 'You can read this chat but not continue it.';

test('J-08: a project-visible chat is read by project members, the ceiling raise lets editors continue, private takes it all away', { tag: '@collab' }, async ({ users }) => {
  const a = await users.fresh('J8');
  const viewer = await users.newPerson('project_viewer');
  const editor = await users.newPerson('project_editor');

  const project = await a.api.call('POST', '/api/v1/projects', { name: 'e2e-collab-j08 project' });
  expect(project.status, JSON.stringify(project.body)).toBeLessThan(300);
  const projectId: string = project.body.project?._id ?? project.body.project?.id;
  const members = await a.api.call('PUT', `/api/v1/projects/${projectId}/members`, {
    members: [
      { principalId: viewer.actor.userId, principalType: 'user', role: 'viewer' },
      { principalId: editor.actor.userId, principalType: 'user', role: 'editor' },
    ],
  });
  expect(members.status, JSON.stringify(members.body)).toBeLessThan(300);

  // A chat with a consented file, linked to the project but still private to A.
  const chat = await a.api.startChat(chatTitle('j08'));
  const turn = await a.api.call('POST', `/api/v1/conversations/${chat}/messages`, {
    query: 'attach',
    chatMode: 'quick',
    attachments: [{ recordId: 'rec-consented', recordName: 'rec-consented.md' }],
    filesShared: true,
  });
  expect(turn.status).toBe(200);
  expect((await a.api.call('PUT', `/api/v1/conversations/${chat}/project`, { projectId })).status).toBeLessThan(300);
  expect((await viewer.api.getChat(chat)).status).toBe(404);
  const file = () => pdpAllows(viewer.actor, { type: 'chatAttachment', recordId: 'rec-consented', ownerUserId: a.actor.userId, conversationId: chat });
  expect(await file()).toBe(false);

  // A makes it visible to project members in the access panel; the change dialog says who gains access first.
  await openChat(a.page, chat);
  await a.page.getByRole('button', { name: 'Who can access this chat' }).click();
  const panel = a.page.getByRole('dialog', { name: 'Access to this chat' });
  await expect(panel.getByTestId('access-explain')).toContainText('You own this chat.', { timeout: 15_000 });
  await expectNoBlockingViolations(a.page, 'project-access-panel');
  const visible = panel.getByRole('switch', { name: 'Visible to project members' });
  await visible.click();
  const dialog = a.page.getByRole('dialog', { name: 'Review access changes' });
  await expect(dialog.getByText('Checking who is affected…')).toHaveCount(0, { timeout: 15_000 });
  await expect(dialog).toContainText('User ProjectViewer');
  await expect(dialog).toContainText('User ProjectEditor');
  await expectNoBlockingViolations(a.page, 'access-change-dialog-gain');
  await dialog.getByRole('button', { name: 'Apply' }).click();
  await expect(dialog).toHaveCount(0);

  // The project viewer now reads it, and its consented file; the other file rules are PH-07's.
  expect((await viewer.api.getChat(chat)).status).toBe(200);
  expect(await file()).toBe(true);
  await openChat(viewer.page, chat);
  await expect(viewer.page.getByTestId('read-only-banner')).toContainText(READ_ONLY);
  await openChat(editor.page, chat);
  await expect(editor.page.getByTestId('read-only-banner')).toContainText(READ_ONLY);
  await expect(composer(editor.page)).toHaveCount(0);

  // The project owner raises the ceiling: editors can continue, viewers still cannot.
  const raised = await a.api.call('PATCH', `/api/v1/projects/${projectId}`, { projectChatAccess: 'editor' });
  expect(raised.status, JSON.stringify(raised.body)).toBe(200);
  await openChat(editor.page, chat);
  await expect(composer(editor.page)).toBeVisible();
  await expect(editor.page.getByTestId('read-only-banner')).toHaveCount(0);
  await openChat(viewer.page, chat);
  await expect(viewer.page.getByTestId('read-only-banner')).toContainText(READ_ONLY);
  const continued = await editor.api.call('POST', `/api/v1/conversations/${chat}/messages`, { query: 'editor continues', chatMode: 'quick' });
  expect(continued.status, JSON.stringify(continued.body)).toBe(200);

  // Flipping the chat to private in the panel removes chat and file access; the open tab finds out on its next poll.
  await a.page.reload();
  await a.page.getByRole('button', { name: 'Who can access this chat' }).click();
  await a.page.getByRole('dialog', { name: 'Access to this chat' }).getByRole('switch', { name: 'Visible to project members' }).click();
  await expect(dialog.getByText('Checking who is affected…')).toHaveCount(0, { timeout: 15_000 });
  await expect(dialog).toContainText('User ProjectViewer');
  await expectNoBlockingViolations(a.page, 'access-change-dialog-loss');
  await dialog.getByRole('button', { name: 'Apply' }).click();
  await expect(dialog).toHaveCount(0);

  expect((await viewer.api.getChat(chat)).status).toBe(404);
  expect((await editor.api.getChat(chat)).status).toBe(404);
  expect(await file()).toBe(false);
  await expect(viewer.page.getByTestId('read-only-banner')).toContainText(LOST, { timeout: 30_000 });
});
