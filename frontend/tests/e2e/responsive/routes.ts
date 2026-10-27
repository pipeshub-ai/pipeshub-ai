import type { Locator, Page } from '@playwright/test';
import { buildAguiConversation } from '../chat/agui-sse-builder';
import { ACTION_TIMEOUT, openNavIfMobile, type AuditRoute, type SeedIds } from './viewport-audit';
import { SEED_FILE_NAME, SEED_KB_NAME, SEED_MCP_SERVER_NAME } from './seed-responsive';

const CONV_ID = 'conv-responsive-audit-001';
const CONV_TITLE = 'Responsive audit conversation with a long title';
const RICH_CONV_ID = 'conv-responsive-audit-rich';
const RICH_TITLE = 'Compare regional revenue and show the rollout plan';
const MODEL_INFO = { modelKey: 'gpt-4o-mini', modelName: 'GPT-4o mini', chatMode: 'internal_search', modelFriendlyName: 'GPT-4o mini' };
const MODELS = [
  { ...MODEL_INFO, provider: 'openAI', isDefault: true },
  { modelKey: 'claude-sonnet', modelName: 'claude-sonnet-4', modelFriendlyName: 'Claude Sonnet 4', provider: 'anthropic', isDefault: false },
  { modelKey: 'gemini-pro', modelName: 'gemini-2.5-pro', modelFriendlyName: 'Gemini 2.5 Pro with a long friendly name', provider: 'gemini', isDefault: false },
].map((m) => ({ chatMode: 'internal_search', modelType: 'chat', isMultimodal: false, isReasoning: false, ...m }));

/** An answer with the blocks that stress a narrow screen: code, a wide table, a diagram and citations. */
const RICH_ANSWER = [
  'Revenue grew fastest in APAC [1], while Europe held steady [2].',
  '',
  '```python',
  'def growth(rows):',
  '    return {r["region"]: round((r["q3"] - r["q1"]) / r["q1"] * 100, 1) for r in rows}  # percent growth from Q1 to Q3',
  '```',
  '',
  '| Region | Q1 revenue (USD) | Q2 revenue (USD) | Q3 revenue (USD) | New customers | Churn % | NPS | Owner |',
  '| --- | --- | --- | --- | --- | --- | --- | --- |',
  '| North America | 1,820,000 | 1,904,500 | 2,011,300 | 118 | 2.1 | 54 | Priya Sharma |',
  '| Europe | 1,240,000 | 1,251,000 | 1,262,800 | 74 | 3.4 | 48 | Jonas Becker |',
  '| APAC | 640,000 | 802,000 | 1,015,700 | 131 | 4.0 | 61 | Kenji Watanabe |',
  '| LATAM | 410,000 | 455,500 | 498,200 | 39 | 5.6 | 42 | Lucas Silva |',
  '',
  '```mermaid',
  'flowchart LR',
  '  A[Plan rollout] --> B{Region ready?}',
  '  B -- yes --> C[Enable for all users]',
  '  B -- no --> D[Pilot with one team]',
  '  D --> B',
  '```',
  '',
  'See the security review for the open items [2].',
].join('\n');

interface MockConversation {
  id: string;
  title: string;
  answer: string;
  citations: number;
}

const AUDIT_CONVERSATION: MockConversation = {
  id: CONV_ID,
  title: CONV_TITLE,
  answer: 'The audit record is cited here [1]. A long unbroken token follows: responsive_audit_0123456789_0123456789_0123456789.',
  citations: 1,
};
const RICH_CONVERSATION: MockConversation = { id: RICH_CONV_ID, title: RICH_TITLE, answer: RICH_ANSWER, citations: 2 };

/**
 * Stored conversations, shown in the sidebar list and opened by id, whose
 * answers cite the seeded record. Same wire shapes as chat-streaming.spec.ts.
 */
function mockConversations(...convs: MockConversation[]) {
  return async (page: Page, seed: SeedIds): Promise<void> => {
    const now = new Date().toISOString();
    const stored = convs.map((c) => {
      const base = buildAguiConversation({
        conversationId: c.id,
        userMessageId: `${c.id}-user`,
        botMessageId: `${c.id}-bot`,
        question: c.title,
        answer: c.answer,
        modelInfo: MODEL_INFO,
      });
      const citations = Array.from({ length: c.citations }, (_, i) => ({
        citationId: `${c.id}-cit-${i + 1}`,
        citationData: {
          _id: `${c.id}-cit-${i + 1}`,
          content: 'Responsive audit record.',
          chunkIndex: i + 1,
          metadata: {
            recordName: SEED_FILE_NAME,
            recordId: seed.recordId,
            connector: 'KB',
            recordType: 'FILE',
            mimeType: 'text/plain',
            extension: 'txt',
            previewRenderable: true,
            origin: 'UPLOAD',
            orgId: 'org-e2e',
            recordVersion: 0,
          },
          citationType: 'vectordb|document',
          createdAt: now,
          updatedAt: now,
        },
      }));
      return { ...base, messages: base.messages.map((m) => (m.messageType === 'bot_response' ? { ...m, citations } : m)) };
    });
    const listItems = stored.map((c) => ({ ...c, id: c._id, isOwner: true, messages: undefined }));

    await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
      route.fulfill({ json: { status: 'success', message: 'Success', models: MODELS } }),
    );
    await page.route('**/api/v1/conversations*', (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      const owned = new URL(route.request().url()).searchParams.get('source') !== 'shared';
      const list = owned ? listItems : [];
      return route.fulfill({
        json: {
          conversations: list,
          source: owned ? 'owned' : 'shared',
          pagination: { page: 1, limit: 20, totalCount: list.length, totalPages: 1, hasNextPage: false, hasPrevPage: false },
        },
      });
    });
    for (const conversation of stored) {
      await page.route(
        (url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${conversation._id}`,
        (route) => {
          if (route.request().method() !== 'GET') return route.fallback();
          return route.fulfill({
            json: { conversation: { ...conversation, id: conversation._id, access: { isOwner: true, accessLevel: 'OWNER' } }, filters: {}, meta: {} },
          });
        },
      );
    }
    // The made-up user ids on the mocked conversation would otherwise come back 400.
    await page.route('**/api/v1/users/by-ids', (route) =>
      route.request().method() === 'POST' ? route.fulfill({ json: [] }) : route.fallback(),
    );
  };
}

const mockChat = mockConversations(AUDIT_CONVERSATION);
const mockRichChat = mockConversations(RICH_CONVERSATION, AUDIT_CONVERSATION);

const CHAT_CONVERSATION = `/chat/?conversationId=${CONV_ID}`;
const RICH_CHAT = `/chat/?conversationId=${RICH_CONV_ID}`;

function icon(page: Page | Locator, name: string) {
  return page.locator('span.material-icons-outlined, span.material-icons', { hasText: new RegExp(`^${name}$`) });
}

const NO_PROJECT = (seed: SeedIds) => (seed.projectId ? undefined : 'the API did not create the audit project');
const NO_AGENT = (seed: SeedIds) => (seed.agentId ? undefined : 'no AI model on the audit stack');
const NO_MCP_SERVER = (seed: SeedIds) => (seed.mcpServerId ? undefined : 'the API did not create the audit MCP server');
const KB_COLLECTION = (s: SeedIds) => `/knowledge-base/?nodeType=app&nodeId=${s.kbId}`;

/** Opens the knowledge base "New" menu (phones label the icon button without its icon name). */
async function openKbNewMenu(page: Page): Promise<void> {
  await page.getByRole('button', { name: /^(add )?New$/ }).click({ timeout: ACTION_TIMEOUT });
}

/** Opens the "…" menu on the row (or card) that shows `text`. */
async function openRowMenu(page: Page, text: string | RegExp): Promise<void> {
  const row = page.getByRole('row').filter({ hasText: text }).filter({ visible: true }).first();
  await row.waitFor({ timeout: ACTION_TIMEOUT });
  if (!(await page.evaluate(() => matchMedia('(hover: none)').matches))) await row.hover({ timeout: ACTION_TIMEOUT });
  // People tables name the trigger "More options", knowledge base rows "More options for <name>";
  // other tables still expose the icon ligature.
  await row.getByRole('button', { name: /^(more_horiz|More options( for .+)?)$/ }).click({ timeout: ACTION_TIMEOUT });
}

/** Opens the mobile drawer, like drawer-open, on any page. */
async function openDrawer(page: Page): Promise<void> {
  const hamburger = page.getByRole('button', { name: 'Open sidebar', exact: true });
  if ((page.viewportSize()?.width ?? 0) <= 768 || (await hamburger.isVisible())) {
    await hamburger.click({ timeout: ACTION_TIMEOUT });
  }
}

/** Leaf `/workspace/*` pages that render in this edition (index redirects left out). */
const WORKSPACE_PAGES = [
  'actions/personal',
  'actions/team',
  'ai-models',
  'archived-chats',
  'authentication',
  'bots',
  'connectors/personal',
  'connectors/team',
  'developer-settings/oauth2',
  'developer-settings/personal-access-tokens',
  'general',
  'groups',
  'labs',
  'mail',
  'mcp-servers/personal',
  'mcp-servers/team',
  'profile',
  'prompts',
  'service-accounts',
  'services',
  'skills/personal',
  'teams',
  'users',
  'web-search',
];

export const ROUTES: AuditRoute[] = [
  { id: 'chat', path: '/chat/' },
  { id: 'chat-conversation', path: CHAT_CONVERSATION, setup: mockChat },
  { id: 'chat-agent', path: (s) => `/chat/?agentId=${s.agentId}`, skip: NO_AGENT },
  { id: 'kb-collections', path: '/knowledge-base/' },
  { id: 'kb-collection', path: (s) => `/knowledge-base/?nodeType=app&nodeId=${s.kbId}` },
  { id: 'kb-all-records', path: '/knowledge-base/?view=all-records' },
  {
    id: 'kb-grid',
    path: (s) => `/knowledge-base/?nodeType=app&nodeId=${s.kbId}`,
    action: async (page) => {
      const toggle = page.getByRole('radio').filter({ has: icon(page, 'grid_on') });
      if (await toggle.count()) {
        await toggle.click({ timeout: ACTION_TIMEOUT });
      } else {
        // Phones keep the view toggle in the header's overflow menu; each row's menu is "More options for <name>".
        await page.getByRole('button', { name: 'More options', exact: true }).click({ timeout: ACTION_TIMEOUT });
        await page.getByRole('menuitem', { name: 'Grid view' }).click({ timeout: ACTION_TIMEOUT });
      }
    },
  },
  { id: 'kb-recently-deleted', path: (s) => `/knowledge-base/recently-deleted/?kbId=${s.kbId}` },
  { id: 'artifacts', path: '/artifacts/' },
  { id: 'projects', path: '/projects/' },
  { id: 'project-workspace', path: (s) => `/projects/?projectId=${s.projectId}`, skip: NO_PROJECT },
  {
    id: 'oauth-consent',
    // The consent screen needs no real client: the authorize call is answered with a made-up one.
    path: '/oauth/authorize/?client_id=responsive-audit&redirect_uri=https%3A%2F%2Fexample.com%2Fcallback&scope=records%3Aread&state=audit',
    setup: async (page) => {
      await page.route('**/api/v1/oauth2/authorize*', (route) =>
        route.request().method() === 'GET'
          ? route.fulfill({
              json: {
                requiresConsent: true,
                consentData: {
                  app: { name: 'Responsive audit client with a deliberately long name', description: 'Used by the viewport audit' },
                  scopes: [{ name: 'records:read', description: 'Read your records', category: 'Records' }],
                  user: { email: 'audit@example.com', name: 'Audit User' },
                  workspace: { name: 'Audit workspace' },
                  redirectUri: 'https://example.com/callback',
                  state: 'audit',
                },
                codeChallenge: 'audit',
                codeChallengeMethod: 'S256',
              },
            })
          : route.fallback(),
      );
    },
  },
  { id: 'agents', path: '/agents/' },
  { id: 'agents-new', path: '/agents/new/' },
  { id: 'agents-edit', path: (s) => `/agents/edit/?agentKey=${s.agentId}`, skip: NO_AGENT },
  { id: 'record', path: (s) => `/record/?recordId=${s.recordId}` },
  {
    id: 'onboarding',
    path: '/onboarding/?step=ai-model',
    // The org is already set up, so the page would redirect away; report it as
    // not set up (read-only: the status is never written).
    setup: async (page) => {
      await page.route('**/api/v1/org/onboarding-status', (route) =>
        route.request().method() === 'GET'
          ? route.fulfill({ json: { status: 'notConfigured' } })
          : route.abort(),
      );
    },
  },
  ...WORKSPACE_PAGES.map((p) => ({ id: `workspace-${p.replace(/\//g, '-')}`, path: `/workspace/${p}/` })),

  // Interaction scenarios
  {
    id: 'drawer-open',
    path: '/chat/',
    // Phones must have the hamburger; wider screens may show the sidebar inline instead.
    action: openDrawer,
  },
  {
    id: 'notifications-open',
    path: '/chat/',
    action: async (page) => {
      await openNavIfMobile(page);
      await page.getByRole('button', { name: /^Inbox/ }).click({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'workspace-menu-appearance',
    path: '/chat/',
    action: async (page) => {
      await openNavIfMobile(page);
      await icon(page, 'keyboard_arrow_up').last().click({ timeout: ACTION_TIMEOUT });
      await page.getByText('Appearance').first().click({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'kb-select-two',
    path: (s) => `/knowledge-base/?nodeType=app&nodeId=${s.kbId}`,
    action: async (page) => {
      const boxes = page.getByRole('checkbox').filter({ visible: true });
      await boxes.nth(1).waitFor({ timeout: ACTION_TIMEOUT }).catch(() => undefined);
      const count = await boxes.count();
      if (count < 2) throw new Error(`needs two selectable rows, found ${count} checkboxes`);
      // The last two are rows; the first may be select-all.
      await boxes.nth(count - 2).click({ timeout: ACTION_TIMEOUT });
      await boxes.nth(count - 1).click({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'chat-row-menu',
    path: CHAT_CONVERSATION,
    setup: mockChat,
    action: async (page) => {
      await openNavIfMobile(page);
      const row = page.getByRole('link', { name: CONV_TITLE }).filter({ visible: true }).first();
      // Recents may start collapsed.
      if (!(await row.isVisible())) await page.getByText('Recents', { exact: true }).click({ timeout: ACTION_TIMEOUT });
      await row.waitFor({ timeout: ACTION_TIMEOUT });
      // A touch screen cannot hover, so there the menu button must already show.
      if (!(await page.evaluate(() => matchMedia('(hover: none)').matches))) await row.hover({ timeout: ACTION_TIMEOUT });
      // The agents list above has its own menu; open the one on this conversation's row.
      const rowMenu = row.locator('xpath=ancestor::*[.//span[normalize-space(.)="more_horiz"]][1]');
      await icon(rowMenu, 'more_horiz').filter({ visible: true }).first().click({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'agent-palette',
    // The builder of a saved agent: /agents/new first asks for a name in a dialog.
    path: (s) => `/agents/edit/?agentKey=${s.agentId}`,
    skip: NO_AGENT,
    // The palette is open by default on wider screens. On phones it opens from
    // the "Add to canvas" button; tap it so the open palette is what gets measured.
    action: async (page) => {
      // Palette rows carry their own "Add to canvas" buttons; only open a closed palette.
      if (await page.getByPlaceholder(/search nodes/i).isVisible()) return;
      const opener = page.getByRole('button', { name: /^(show palette|add to canvas)$/i }).filter({ visible: true });
      if (await opener.count()) await opener.first().click({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'invite-panel',
    // Opened by URL: the Invite button stays disabled until the org has SMTP,
    // and the panel itself does not need it.
    path: '/workspace/users/?panel=invite',
  },
  {
    id: 'search-chats',
    path: '/chat/',
    action: async (page) => {
      await openNavIfMobile(page);
      await page.getByText('Search chats', { exact: true }).click({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'citation-preview',
    path: CHAT_CONVERSATION,
    setup: mockChat,
    action: async (page) => {
      await page.getByText('Citation', { exact: true }).last().click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('button', { name: 'Preview', exact: true }).first().click({ timeout: ACTION_TIMEOUT });
    },
  },

  // Dialogs
  {
    id: 'dialog-create-collection',
    path: '/knowledge-base/',
    action: async (page) => {
      await openKbNewMenu(page);
      await page.getByRole('menuitem', { name: /New folder|New collection/i }).click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('dialog', { name: 'Create Collection' }).waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'dialog-upload',
    path: KB_COLLECTION,
    action: async (page) => {
      await openKbNewMenu(page);
      await page.getByRole('menuitem', { name: /Upload data/i }).click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('dialog', { name: 'Upload Data' }).waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'dialog-delete-collection',
    path: '/knowledge-base/',
    action: async (page) => {
      await openRowMenu(page, SEED_KB_NAME);
      await page.getByRole('menuitem', { name: /Delete/ }).click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('dialog', { name: 'Delete Confirmation' }).waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'dialog-connector-setup',
    path: '/workspace/connectors/team/',
    // The first step of the setup wizard of the first connector in the catalog.
    action: async (page) => {
      await page.getByRole('button', { name: 'add Set up' }).first().click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('tab', { name: 'Authenticate' }).waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'dialog-create-token',
    path: '/workspace/developer-settings/personal-access-tokens/',
    action: async (page) => {
      // With no tokens yet the empty state repeats the header's button.
      await page.getByRole('button', { name: 'add New token' }).first().click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('dialog', { name: 'New personal access token' }).waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'dialog-ai-model-add',
    path: '/workspace/ai-models/',
    action: async (page) => {
      await page.getByRole('button', { name: 'add Configure' }).first().click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('dialog').filter({ visible: true }).first().waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'dialog-mcp-add',
    path: '/workspace/mcp-servers/team/',
    // Where the API refuses MCP servers, the page is not offered either.
    skip: NO_MCP_SERVER,
    action: async (page) => {
      await page.getByRole('button', { name: 'add New server' }).click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('dialog', { name: 'New MCP server' }).waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'dialog-delete-mcp',
    path: '/workspace/mcp-servers/team/',
    skip: NO_MCP_SERVER,
    action: async (page) => {
      const card = page.locator('div').filter({ hasText: SEED_MCP_SERVER_NAME }).filter({ has: page.getByRole('button', { name: 'delete Delete' }) }).last();
      await card.getByRole('button', { name: 'delete Delete' }).click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('alertdialog').or(page.getByRole('dialog')).filter({ visible: true }).first().waitFor({ timeout: ACTION_TIMEOUT });
    },
  },

  // Menus and sheets
  {
    id: 'menu-workspace-language',
    path: '/chat/',
    action: async (page) => {
      await openNavIfMobile(page);
      await icon(page, 'keyboard_arrow_up').last().click({ timeout: ACTION_TIMEOUT });
      await page.getByText('Language').first().click({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'menu-chat-plus',
    path: '/chat/',
    action: async (page) => {
      await page.getByRole('button', { name: 'Attach files and capabilities' }).click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('menuitem', { name: /Attach files/ }).waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'menu-model-picker',
    path: '/chat/',
    setup: mockChat,
    action: async (page) => {
      const chip = page.getByRole('button', { name: /GPT-4o mini|AI models/i }).filter({ visible: true });
      if (await chip.count()) {
        await chip.first().click({ timeout: ACTION_TIMEOUT });
      } else {
        // Phones keep the model picker under the composer's More options sheet.
        await page.getByRole('button', { name: 'More options' }).click({ timeout: ACTION_TIMEOUT });
        await page.getByText('Models', { exact: true }).click({ timeout: ACTION_TIMEOUT });
      }
      await page.getByText('Claude Sonnet 4').first().waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'menu-kb-filter',
    path: KB_COLLECTION,
    action: async (page) => {
      await page.getByRole('button', { name: /Type/ }).filter({ visible: true }).first().click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('dialog').filter({ visible: true }).first().waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'menu-kb-row',
    path: '/knowledge-base/',
    action: async (page) => {
      await openRowMenu(page, SEED_KB_NAME);
      await page.getByRole('menu').filter({ visible: true }).first().waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'menu-users-row',
    path: '/workspace/users/',
    action: async (page) => {
      await openRowMenu(page, /@/);
      await page.getByRole('menuitem').filter({ visible: true }).first().waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'menu-connectors-row',
    path: '/workspace/connectors/team/',
    // The catalog rows have only a Setup button; a row menu appears once a connector is configured.
    skip: () => 'connector rows get a menu only once a connector is configured with real credentials',
  },
  {
    id: 'menu-mcp-row',
    path: '/workspace/mcp-servers/team/',
    skip: NO_MCP_SERVER,
    action: async (page) => {
      const card = page.locator('div').filter({ hasText: SEED_MCP_SERVER_NAME }).filter({ has: page.getByRole('button', { name: 'settings Manage' }) }).last();
      await card.getByRole('button', { name: 'settings Manage' }).click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('button', { name: 'More actions' }).click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('menu').filter({ visible: true }).first().waitFor({ timeout: ACTION_TIMEOUT });
    },
  },

  // States
  { id: 'state-empty-collection', path: (s) => `/knowledge-base/?nodeType=app&nodeId=${s.emptyKbId}` },
  {
    id: 'state-empty-search',
    path: '/knowledge-base/',
    action: async (page) => {
      await page.getByRole('button', { name: /^(search )?Find$/ }).click({ timeout: ACTION_TIMEOUT });
      await page.keyboard.type('zzqx-no-such-file');
      await page.getByText('No results found').waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  { id: 'state-not-found', path: '/responsive-audit-no-such-page/', notFound: true },
  {
    id: 'chat-rich-answer',
    path: RICH_CHAT,
    setup: mockRichChat,
    action: async (page) => {
      await page.getByTitle('Expand diagram').first().waitFor({ state: 'attached', timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'chat-mermaid-fullscreen',
    path: RICH_CHAT,
    setup: mockRichChat,
    action: async (page) => {
      await page.getByTitle('Expand diagram').first().click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('dialog').filter({ visible: true }).first().waitFor({ timeout: ACTION_TIMEOUT });
    },
  },
  {
    id: 'chat-table-fullscreen',
    path: RICH_CHAT,
    setup: mockRichChat,
    action: async (page) => {
      await page.getByTitle('Expand table').first().click({ timeout: ACTION_TIMEOUT });
      await page.getByRole('dialog').filter({ visible: true }).first().waitFor({ timeout: ACTION_TIMEOUT });
    },
  },

  // Each sidebar in the mobile drawer (the chat one is drawer-open)
  { id: 'drawer-kb', path: '/knowledge-base/', action: openDrawer },
  { id: 'drawer-projects', path: '/projects/', action: openDrawer },
  { id: 'drawer-workspace', path: '/workspace/general/', action: openDrawer },
];
