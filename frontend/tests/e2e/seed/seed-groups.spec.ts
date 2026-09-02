import { test, expect } from '../fixtures/api-context.fixture';
import { postWithRetry } from '../helpers/api-retry.helper';
import { GroupType } from '@/app/(main)/workspace/groups/types';

const TOTAL_GROUPS = 30;

function groupName(index: number): string {
  return `E2E Group ${String(index).padStart(3, '0')}`;
}

test.describe.serial('Seed Groups', () => {
  // OSS Groups page is an Enterprise placeholder — seed via API only.
  test('create 30 groups via API', async ({ apiContext }) => {
    test.setTimeout(5 * 60_000);

    let ensured = 0;
    for (let j = 0; j < TOTAL_GROUPS; j++) {
      const index = j + 1;
      const response = await postWithRetry(apiContext, '/api/v1/userGroups', { name: groupName(index), type: GroupType.CUSTOM });
      if (response.ok()) {
        ensured += 1;
        continue;
      }
      const body = await response.text();
      if (/already exists/i.test(body)) {
        ensured += 1;
        continue;
      }
      throw new Error(`POST /api/v1/userGroups failed [${response.status()}] for "${groupName(index)}": ${body}`);
    }
    expect(ensured).toBe(TOTAL_GROUPS);
  });
});
