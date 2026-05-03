import { test, expect } from '../fixtures/api-context.fixture';

const EMAIL_DOMAIN = 'e2etest.pipeshub.local';

test.describe.serial('Cleanup E2E Test Data', () => {
  test('delete seeded users', async ({ apiContext }) => {
    let page = 1;
    let deleted = 0;

    // Paginate through all users matching e2e pattern
    while (true) {
      const response = await apiContext.get('/api/v1/users', {
        params: { page, limit: 100, search: 'e2e-user' },
      });
      if (!response.ok()) break;

      const data = await response.json();
      const users = data.users ?? [];
      if (users.length === 0) break;

      const e2eUsers = users.filter(
        (u: { email?: string }) => u.email?.endsWith(`@${EMAIL_DOMAIN}`)
      );

      for (const user of e2eUsers) {
        const id = user._id ?? user.id;
        if (!id) continue;
        const delResponse = await apiContext.delete(`/api/v1/users/${id}`);
        if (delResponse.ok()) deleted++;
      }

      if (users.length < 100) break;
      page++;
    }

    console.log(`Deleted ${deleted} e2e users`);
  });

  test('delete seeded groups', async ({ apiContext }) => {
    let page = 1;
    let deleted = 0;

    while (true) {
      const response = await apiContext.get('/api/v1/userGroups', {
        params: { page, limit: 100, search: 'E2E Group' },
      });
      if (!response.ok()) break;

      const data = await response.json();
      const groups = data.groups ?? [];
      if (groups.length === 0) break;

      const e2eGroups = groups.filter(
        (g: { name?: string }) => g.name?.startsWith('E2E Group')
      );

      for (const group of e2eGroups) {
        const id = group._id ?? group.id;
        if (!id) continue;
        const delResponse = await apiContext.delete(`/api/v1/userGroups/${id}`);
        if (delResponse.ok()) deleted++;
      }

      if (groups.length < 100) break;
      page++;
    }

    console.log(`Deleted ${deleted} e2e groups`);
  });

  test('delete seeded teams', async ({ apiContext }) => {
    let page = 1;
    let deleted = 0;

    while (true) {
      const response = await apiContext.get('/api/v1/teams/user/teams', {
        params: { page, limit: 100, search: 'E2E Team' },
      });
      if (!response.ok()) break;

      const data = await response.json();
      const teams = data.teams ?? [];
      if (teams.length === 0) break;

      const e2eTeams = teams.filter(
        (t: { name?: string }) => t.name?.startsWith('E2E Team')
      );

      for (const team of e2eTeams) {
        const id = team._id ?? team.id;
        if (!id) continue;
        const delResponse = await apiContext.delete(`/api/v1/teams/${id}`);
        if (delResponse.ok()) deleted++;
      }

      if (teams.length < 100) break;
      page++;
    }

    console.log(`Deleted ${deleted} e2e teams`);
  });
});
