import { test, expect } from './support/two-users.fixture';
import { fake } from './support/stack';
import { composer, openChat } from './support/chat-ui';
import { expectNoBlockingViolations } from './support/a11y';
import { chatTitle, shareChat } from './support/collab.helper';

/**
 * J-06: team share and membership change. The API half (every denial and cache rule) is PH-06's
 * `integration_test_j06_team_membership.py`; this is what the people see. Teams live in the connectors fake.
 */
test.describe.configure({ mode: 'serial' });

const LOST = 'You no longer have access to this chat. What you see here is kept until you leave it.';
const CACHE_TTL_S = 30;
const BOUND_S = 60;

test('J-06: a member removed through the Teams route loses the open chat on the next poll; a member added opens it', { tag: '@collab' }, async ({ users }) => {
  const { a, c } = users;
  const member = await users.newPerson('team_writer');
  const team = await fake.addTeam('e2e-collab-j06 writers', { owner: 'OWNER', team_writer: 'WRITER' });
  const chat = await a.api.startChat(chatTitle('j06'));
  await shareChat(a.api, chat, { teamId: team.teamId, level: 'write' });

  // The member reads and may continue, as a team member; the outsider has nothing.
  await openChat(member.page, chat);
  await expect(composer(member.page)).toBeVisible();
  expect((await c.api.getChat(chat)).status).toBe(404);

  // A removes the member through the Node Teams route: the very next request is denied (no stale window).
  const removed = await a.api.call('PUT', `/api/v1/teams/${team.teamId}`, { removeUserIds: [member.actor.userId] });
  expect(removed.status, JSON.stringify(removed.body)).toBe(200);
  expect((await member.api.getChat(chat)).status).toBe(404);

  // The member's open tab finds out on its next poll: banner instead of the composer, history kept.
  await expect(member.page.getByTestId('read-only-banner')).toContainText(LOST, { timeout: 30_000 });
  await expect(composer(member.page)).toHaveCount(0);
  await expect(member.page.getByText(chatTitle('j06'))).toBeVisible();
  await expectNoBlockingViolations(member.page, 'team-member-removed');

  // Adding the outsider through the same route opens the chat on their next request, at the team's level.
  const added = await a.api.call('PUT', `/api/v1/teams/${team.teamId}`, { addUserRoles: [{ userId: c.actor.userId, role: 'WRITER' }] });
  expect(added.status, JSON.stringify(added.body)).toBe(200);
  expect((await c.api.getChat(chat)).status).toBe(200);
  await openChat(c.page, chat);
  await expect(composer(c.page)).toBeVisible();
  await expect(c.page.getByTestId('read-only-banner')).toHaveCount(0);
});

test('J-06: a change outside Node is served from cache for at most 60 s, then denied (cache state, no real wait)', { tag: '@collab' }, async ({ users }) => {
  const { a } = users;
  const member = await users.newPerson('team_reader');
  const team = await fake.addTeam('e2e-collab-j06 readers', { owner: 'OWNER', team_reader: 'READER' });
  const chat = await a.api.startChat(chatTitle('j06-outside'));
  await shareChat(a.api, chat, { teamId: team.teamId, level: 'read' });

  await openChat(member.page, chat);
  await expect(member.page.getByTestId('read-only-banner')).toContainText('You can read this chat but not continue it.');
  const orgId = member.actor.orgId;
  const teamKeys = `*teamids:v1:${orgId}:${member.actor.userId}:*`;
  const decisionKeys = `*authz:v1:${orgId}:*${member.actor.userId}*`;

  // The connectors side drops the member; Node is not told.
  await fake.changeTeamMember(team.teamId, member.actor.userId, true);
  expect((await member.api.getChat(chat)).status).toBe(200);
  await expect(member.page.getByTestId('read-only-banner')).not.toContainText('no longer have access');

  // Both caches hold an entry for the member and each lives at most 30 s, so the answer is stale for at most 30 + 30 s.
  const teamCache = await fake.cache(teamKeys);
  const decisionCache = await fake.cache(decisionKeys);
  expect(Object.keys(teamCache).length, 'a team-id cache entry').toBeGreaterThan(0);
  expect(Object.keys(decisionCache).length, 'a decision cache entry').toBeGreaterThan(0);
  for (const ttl of [...Object.values(teamCache), ...Object.values(decisionCache)]) {
    expect(ttl).toBeGreaterThan(0);
    expect(ttl).toBeLessThanOrEqual(CACHE_TTL_S);
  }
  expect(2 * CACHE_TTL_S).toBeLessThanOrEqual(BOUND_S);

  // With the decision entry gone the answer is recomputed from the cached team ids and is still stale.
  await fake.deleteCache(decisionKeys);
  expect((await member.api.getChat(chat)).status).toBe(200);

  // Both layers expired, as 30 s + 30 s would leave them: the teams are resolved again and the chat is denied.
  await fake.deleteCache(teamKeys, decisionKeys);
  expect((await member.api.getChat(chat)).status).toBe(404);
  await expect(member.page.getByTestId('read-only-banner')).toContainText(LOST, { timeout: 30_000 });
});
