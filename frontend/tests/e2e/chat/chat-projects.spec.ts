/**
 * chat-projects.spec.ts
 *
 * e2e coverage for the Projects feature (Phase 3 frontend):
 *  - Sidebar `ProjectsSection` lists the caller's projects.
 *  - `/chat/?projectId=…` renders `ProjectWorkspace` with project details.
 *  - Sending a message from the project workspace starts a project-scoped
 *    conversation (`projectId` on the stream request) and, once the
 *    conversation is created, keeps `?projectId=` in the URL alongside
 *    `conversationId` so `ProjectScopedChatSidebar` stays active.
 *
 * All backend calls are intercepted with page.route(); the SSE body uses the
 * shared AG-UI frame builder (see agui-sse-builder.ts) since `chat/api.ts`
 * always negotiates `protocol: 'agui'`.
 */

import { test, expect } from '../fixtures/base.fixture';
import { buildAguiSseBody } from './agui-sse-builder';

const PROJECT_ID = 'proj-e2e-001';
const PROJECT_NAME = 'Q3 Launch Plan';
const CONV_ID = 'conv-e2e-project-001';
const MSG_USER_ID = 'msg-user-e2e-001';
const MSG_BOT_ID = 'msg-bot-e2e-001';

const MOCK_LLMS = {
  status: 'success',
  models: [
    {
      modelType: 'chat',
      provider: 'openAI',
      modelName: 'GPT-4o mini',
      modelKey: 'gpt-4o-mini',
      isMultimodal: false,
      isReasoning: false,
      isDefault: true,
      modelFriendlyName: 'GPT-4o mini',
    },
  ],
  message: 'Success',
};

const MOCK_MODEL_INFO = {
  modelKey: 'gpt-4o-mini',
  modelName: 'GPT-4o mini',
  chatMode: 'internal_search',
  modelFriendlyName: 'GPT-4o mini',
};

const MOCK_CONVERSATIONS_EMPTY = {
  conversations: [],
  source: 'owned',
  pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
};

function makeProjectSummary() {
  return {
    _id: PROJECT_ID,
    orgId: 'org-e2e',
    userId: 'user-e2e',
    name: PROJECT_NAME,
    description: 'Coordinate the Q3 product launch',
    visibility: 'private',
    chatSharing: 'private',
    isPinned: false,
    isArchived: false,
    lastActivityAt: Date.now(),
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    role: 'owner',
    conversationCount: 0,
  };
}

function makeProjectDetail() {
  const { conversationCount: _drop, ...summary } = makeProjectSummary();
  return {
    ...summary,
    instructions: 'Always cite the launch doc when answering.',
    files: [],
    members: [],
  };
}

async function mockBaselineApis(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_LLMS),
    }),
  );

  await page.route('**/api/v1/conversations*', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_CONVERSATIONS_EMPTY),
      });
    }
    return route.continue();
  });
}

/** Sidebar preview (`ProjectsSection`) + workspace detail + empty recent conversations. */
async function mockProjectApis(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/projects?*', (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        projects: [makeProjectSummary()],
        pagination: { page: 1, limit: 10, totalCount: 1, totalPages: 1 },
      }),
    });
  });

  await page.route(`**/api/v1/projects/${PROJECT_ID}`, (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ project: makeProjectDetail() }),
    });
  });

  await page.route(`**/api/v1/projects/${PROJECT_ID}/conversations*`, (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        conversations: [],
        pagination: { page: 1, limit: 5, totalCount: 0, totalPages: 0 },
      }),
    });
  });
}

async function sendMessage(page: import('@playwright/test').Page, message: string) {
  const textarea = page.locator('textarea').last();
  await textarea.click();
  await textarea.fill(message);
  await textarea.press('Enter');
}

const SUGGESTED_USER_ID = 'user-e2e-suggested-1';
const SUGGESTED_USER_NAME = 'Jane Suggested';
const SUGGESTED_USER_EMAIL = 'jane.suggested@example.com';
const OWNER_USER_ID = 'user-e2e';

/**
 * Mocks the org-user lookups the generic `ShareSidebar` needs
 * (`app/components/share/`) plus the projects members endpoints, so the
 * "Share" flow in `ProjectWorkspace` (see `share-adapter.ts`'s
 * `createProjectShareAdapter`) can run end-to-end against a stub backend.
 */
async function mockSharingApis(page: import('@playwright/test').Page) {
  let members: Array<{ principalType: string; principalId: string; role: string }> = [];

  await page.route(`**/api/v1/projects/${PROJECT_ID}/members`, (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ members }),
      });
    }
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON() as {
        members: Array<{ principalId: string; role: string }>;
      };
      members = body.members.map((m) => ({
        principalType: 'user',
        principalId: m.principalId,
        role: m.role,
      }));
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ members }),
      });
    }
    return route.continue();
  });

  await page.route('**/api/v1/users?*', (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        users: [
          {
            userId: SUGGESTED_USER_ID,
            name: SUGGESTED_USER_NAME,
            email: SUGGESTED_USER_EMAIL,
          },
        ],
        pagination: { totalCount: 1 },
      }),
    });
  });

  await page.route('**/api/v1/users/by-ids', (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    const { userIds } = route.request().postDataJSON() as { userIds: string[] };
    const byId: Record<string, { userId: string; name: string; email: string }> = {
      [OWNER_USER_ID]: { userId: OWNER_USER_ID, name: 'Project Owner', email: 'owner@example.com' },
      [SUGGESTED_USER_ID]: {
        userId: SUGGESTED_USER_ID,
        name: SUGGESTED_USER_NAME,
        email: SUGGESTED_USER_EMAIL,
      },
    };
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(userIds.map((id) => byId[id]).filter(Boolean)),
    });
  });
}

test.describe('Projects — sidebar + workspace (mocked backend)', () => {
  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await mockProjectApis(page);
  });

  test('sidebar lists the project under "Projects"', async ({ page }) => {
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await expect(page.getByText(PROJECT_NAME).first()).toBeVisible({ timeout: 10_000 });
  });

  test('clicking the project in the sidebar navigates to ?projectId=', async ({ page }) => {
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await page.getByText(PROJECT_NAME).first().click();
    await expect(page).toHaveURL(new RegExp(`projectId=${PROJECT_ID}`), { timeout: 10_000 });
  });

  test('project workspace renders name, description, and instructions', async ({ page }) => {
    await page.goto(`/chat/?projectId=${PROJECT_ID}`);
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await expect(page.getByText(PROJECT_NAME).first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Coordinate the Q3 product launch').first()).toBeVisible();
    await expect(
      page.getByText('Always cite the launch doc when answering.').first(),
    ).toBeVisible();
  });

  test('composer is available directly from the project workspace', async ({ page }) => {
    await page.goto(`/chat/?projectId=${PROJECT_ID}`);
    await page.waitForSelector('textarea', { timeout: 15_000 });
    const textarea = page.locator('textarea').last();
    await expect(textarea).toBeVisible();
  });
});

test.describe('Projects — starting a new project-scoped conversation', () => {
  const QUESTION = 'What is the launch date?';
  const ANSWER = 'The Q3 launch is targeted for September 30.';

  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await mockProjectApis(page);
  });

  test('stream request for a brand-new chat includes projectId', async ({ page }) => {
    let capturedBody: Record<string, unknown> | null = null;

    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      capturedBody = route.request().postDataJSON();
      return route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'X-Accel-Buffering': 'no',
        },
        body: buildAguiSseBody({
          conversationId: CONV_ID,
          userMessageId: MSG_USER_ID,
          botMessageId: MSG_BOT_ID,
          question: QUESTION,
          answer: ANSWER,
          modelInfo: MOCK_MODEL_INFO,
          requestId: 'req-e2e-project-001',
        }),
      });
    });

    await page.goto(`/chat/?projectId=${PROJECT_ID}`);
    await page.waitForSelector('textarea', { timeout: 15_000 });

    await sendMessage(page, QUESTION);

    await expect(page.locator(`text=${ANSWER}`).first()).toBeVisible({ timeout: 20_000 });
    expect(capturedBody).not.toBeNull();
    expect((capturedBody as unknown as { projectId?: string })?.projectId).toBe(PROJECT_ID);
  });

  test('URL keeps ?projectId= alongside conversationId once the conversation is created', async ({
    page,
  }) => {
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'X-Accel-Buffering': 'no',
        },
        body: buildAguiSseBody({
          conversationId: CONV_ID,
          userMessageId: MSG_USER_ID,
          botMessageId: MSG_BOT_ID,
          question: QUESTION,
          answer: ANSWER,
          modelInfo: MOCK_MODEL_INFO,
          requestId: 'req-e2e-project-002',
        }),
      });
    });

    await page.goto(`/chat/?projectId=${PROJECT_ID}`);
    await page.waitForSelector('textarea', { timeout: 15_000 });

    await sendMessage(page, QUESTION);

    await expect(page).toHaveURL(new RegExp(`projectId=${PROJECT_ID}.*conversationId=${CONV_ID}`), {
      timeout: 15_000,
    });
  });
});

test.describe('Projects — sharing (owner)', () => {
  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await mockProjectApis(page);
    await mockSharingApis(page);
  });

  test('owner can open Share, add a member, and see them in the member count', async ({ page }) => {
    await page.goto(`/chat/?projectId=${PROJECT_ID}`);
    await page.waitForSelector('textarea', { timeout: 15_000 });

    // makeProjectDetail() sets role: 'owner', so the Share entry point is visible.
    // Not yet scoped to a dialog since the dialog isn't open — only the
    // workspace's own "Share" button matches at this point.
    await page.getByRole('button', { name: 'Share', exact: true }).click();

    // Generic ShareSidebar (app/components/share/) driven by createProjectShareAdapter.
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText('Share Project')).toBeVisible({ timeout: 10_000 });

    await dialog
      .getByPlaceholder('Emails, teams or names (separated by commas)')
      .fill('Jane');
    await dialog.getByText(SUGGESTED_USER_NAME).first().click();

    const putPromise = page.waitForRequest(
      (req) =>
        req.url().includes(`/api/v1/projects/${PROJECT_ID}/members`) && req.method() === 'PUT',
    );

    await dialog.getByRole('button', { name: 'Share', exact: true }).click();

    const putRequest = await putPromise;
    const putBody = putRequest.postDataJSON() as {
      members: Array<{ principalId: string; role: string }>;
    };
    expect(putBody.members).toEqual([{ principalId: SUGGESTED_USER_ID, role: 'viewer' }]);

    // Sidebar re-fetches members after a successful share; the new row appears
    // alongside the owner (`isOwner: true` row synthesized in the adapter).
    await expect(dialog.getByText(SUGGESTED_USER_NAME)).toBeVisible({ timeout: 10_000 });
    await expect(dialog.getByText('Project Owner')).toBeVisible();
  });
});
