import type { APIRequestContext, APIResponse } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';

const EMAIL_DOMAIN = 'e2etest.pipeshub.local';

/**
 * The id an entity was listed with. Skipping in silence is how leftovers pile
 * up in the test organisation unseen, so a missing id says so out loud and the
 * caller counts it.
 */
function listedId(
  entity: { _id?: string; id?: string; _key?: string; name?: string; email?: string },
  kind: string,
): string | undefined {
  const id = entity._id ?? entity.id ?? entity._key;
  if (id) return id;
  console.warn(
    `Cannot delete the ${kind} "${entity.name ?? entity.email ?? '(unnamed)'}": ` +
      `the listing gave no id. Delete it by hand, and check whether the ` +
      `listing's shape has changed.`,
  );
  return undefined;
}

/**
 * A listing that failed leaves an unknown number of leftovers behind, so the
 * sweep stops rather than reporting a tidy total it cannot stand behind.
 */
async function requireListing(
  response: APIResponse,
  kind: string,
  page: number,
): Promise<void> {
  if (!response.ok()) {
    throw new Error(
      `Listing ${kind} (page ${page}) failed [${response.status()}]: ` +
        `${await response.text()}. Some test data may still be in the ` +
        `organisation; run this again once the API answers.`,
    );
  }
}

/**
 * Delete one entity. A 404 means a test already removed it, which is fine.
 * Anything else is reported and counted, so one stubborn entity does not stop
 * the sweep clearing everything else.
 */
async function deleteEntity(
  apiContext: APIRequestContext,
  url: string,
  kind: string,
  name: string,
): Promise<'deleted' | 'gone' | 'failed'> {
  const response = await apiContext.delete(url);
  if (response.ok()) return 'deleted';
  if (response.status() === 404) return 'gone';
  console.warn(
    `Could not delete the ${kind} "${name}" [${response.status()}]: ` +
      `${await response.text()}`,
  );
  return 'failed';
}

test.describe.serial('Cleanup E2E Test Data', () => {
  test('delete seeded users', async ({ apiContext }) => {
    let page = 1;
    let deleted = 0;
    let skipped = 0;
    let failed = 0;

    // Paginate through all users matching e2e pattern
    while (true) {
      const response = await apiContext.get('/api/v1/users', {
        params: { page, limit: 100, search: 'e2e-user' },
      });
      await requireListing(response, 'users', page);

      const data = await response.json();
      const users = data.users ?? [];
      if (users.length === 0) break;

      const e2eUsers = users.filter(
        (u: { email?: string }) => u.email?.endsWith(`@${EMAIL_DOMAIN}`)
      );

      for (const user of e2eUsers) {
        const id = listedId(user, 'user');
        if (!id) {
          skipped++;
          continue;
        }
        const outcome = await deleteEntity(
          apiContext,
          `/api/v1/users/${id}`,
          'user',
          user.email ?? id,
        );
        if (outcome === 'deleted') deleted++;
        if (outcome === 'failed') failed++;
      }

      if (users.length < 100) break;
      page++;
    }

    console.log(`Deleted ${deleted} e2e users`);
    // Leftovers are the whole point of this sweep, so they fail it rather than
    // sitting in a log nobody reads.
    expect(
      skipped,
      'e2e users the listing gave no id for; delete them by hand and check the listing shape',
    ).toBe(0);
    expect(
      failed,
      'e2e users that could not be deleted; see the warnings above',
    ).toBe(0);
  });

  test('delete seeded groups', async ({ apiContext }) => {
    let page = 1;
    let deleted = 0;
    let skipped = 0;
    let failed = 0;

    while (true) {
      const response = await apiContext.get('/api/v1/userGroups', {
        params: { page, limit: 100, search: 'E2E Group' },
      });
      await requireListing(response, 'groups', page);

      const data = await response.json();
      const groups = data.groups ?? [];
      if (groups.length === 0) break;

      const e2eGroups = groups.filter(
        (g: { name?: string }) => g.name?.startsWith('E2E Group')
      );

      for (const group of e2eGroups) {
        const id = listedId(group, 'group');
        if (!id) {
          skipped++;
          continue;
        }
        const outcome = await deleteEntity(
          apiContext,
          `/api/v1/userGroups/${id}`,
          'group',
          group.name ?? id,
        );
        if (outcome === 'deleted') deleted++;
        if (outcome === 'failed') failed++;
      }

      if (groups.length < 100) break;
      page++;
    }

    console.log(`Deleted ${deleted} e2e groups`);
    // Leftovers are the whole point of this sweep, so they fail it rather than
    // sitting in a log nobody reads.
    expect(
      skipped,
      'e2e groups the listing gave no id for; delete them by hand and check the listing shape',
    ).toBe(0);
    expect(
      failed,
      'e2e groups that could not be deleted; see the warnings above',
    ).toBe(0);
  });

  test('delete seeded teams', async ({ apiContext }) => {
    let page = 1;
    let deleted = 0;
    let skipped = 0;
    let failed = 0;

    while (true) {
      const response = await apiContext.get('/api/v1/teams/user/teams', {
        params: { page, limit: 100, search: 'E2E Team' },
      });
      await requireListing(response, 'teams', page);

      const data = await response.json();
      const teams = data.teams ?? [];
      if (teams.length === 0) break;

      const e2eTeams = teams.filter(
        (t: { name?: string }) => t.name?.startsWith('E2E Team')
      );

      for (const team of e2eTeams) {
        const id = listedId(team, 'team');
        if (!id) {
          skipped++;
          continue;
        }
        const outcome = await deleteEntity(
          apiContext,
          `/api/v1/teams/${id}`,
          'team',
          team.name ?? id,
        );
        if (outcome === 'deleted') deleted++;
        if (outcome === 'failed') failed++;
      }

      if (teams.length < 100) break;
      page++;
    }

    console.log(`Deleted ${deleted} e2e teams`);
    // Leftovers are the whole point of this sweep, so they fail it rather than
    // sitting in a log nobody reads.
    expect(
      skipped,
      'e2e teams the listing gave no id for; delete them by hand and check the listing shape',
    ).toBe(0);
    expect(
      failed,
      'e2e teams that could not be deleted; see the warnings above',
    ).toBe(0);
  });

  test('delete e2e upload knowledge bases', async ({ apiContext }) => {
    let pageNum = 1;
    let deleted = 0;
    let skipped = 0;
    let failed = 0;

    while (true) {
      const response = await apiContext.get('/api/v1/knowledgeBase', {
        params: { page: pageNum, limit: 100 },
      });
      await requireListing(response, 'knowledge bases', pageNum);

      const data = await response.json();
      const kbs: { id?: string; _key?: string; name?: string }[] =
        data.knowledgeBases ?? data.kbs ?? data.items ?? [];
      if (kbs.length === 0) break;

      const e2eKbs = kbs.filter((kb) => kb.name?.startsWith('E2E Upload'));

      for (const kb of e2eKbs) {
        const id = listedId(kb, 'knowledge base');
        if (!id) {
          skipped++;
          continue;
        }
        const outcome = await deleteEntity(
          apiContext,
          `/api/v1/knowledgeBase/${id}`,
          'knowledge base',
          kb.name ?? id,
        );
        if (outcome === 'deleted') deleted++;
        if (outcome === 'failed') failed++;
      }

      if (kbs.length < 100) break;
      pageNum++;
    }

    console.log(`Deleted ${deleted} e2e upload knowledge bases`);
    // Leftovers are the whole point of this sweep, so they fail it rather than
    // sitting in a log nobody reads.
    expect(
      skipped,
      'e2e upload knowledge bases the listing gave no id for; delete them by hand and check the listing shape',
    ).toBe(0);
    expect(
      failed,
      'e2e upload knowledge bases that could not be deleted; see the warnings above',
    ).toBe(0);
  });
});
