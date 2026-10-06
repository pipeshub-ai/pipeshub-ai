import { test, expect, type Page } from '@playwright/test';
import { NodeApi, fake, stackState } from './support/stack';
import { composer } from './support/chat-ui';
import { COMBOS, VISUAL_ENABLED, makeCast, setCurrentCombo, shot, unreachable, writeIndex, OUT_DIR, type Cast } from './support/visual-ph11';

/**
 * PH-11 screenshot review (11.2 handle field, 11.4 draft card, created toast, redacted notice), desktop and mobile, light and dark.
 * Opt-in: `PCC_VISUAL_TOUR=1 tests/e2e/collab-chats/run.sh visual-ph11`. Files land in `test-results/visual-ph11/`.
 * The builder's own data comes from the stack; only the one agent it opens (and its save) is answered here.
 */
test.skip(!VISUAL_ENABLED, 'PH-11 visual review: set PCC_VISUAL_TOUR=1');
test.describe.configure({ mode: 'serial' });

const AGENT_KEY = 'agent-handle-1';

function agentBody(overrides: Record<string, unknown> = {}) {
  return {
    status: 'success',
    agent: {
      _key: AGENT_KEY, _id: `agentInstances/${AGENT_KEY}`, id: AGENT_KEY, name: 'Offer drafter', handle: 'offer-drafter',
      description: 'Drafts offer letters', startMessage: 'Hi', systemPrompt: 'You draft offers.', instructions: '', models: [],
      tags: [], isActive: true, isDeleted: false, isServiceAccount: false, createdBy: '0000000000000000000000aa', createdAtTimestamp: 1,
      updatedAtTimestamp: 1, toolsets: [], knowledge: [], shareWithOrg: false, access_type: 'INDIVIDUAL', user_role: 'OWNER',
      can_edit: true, can_delete: true, can_share: true, can_view: true, ...overrides,
    },
  };
}

/** The fake Python backend has no knowledge-hub or skills catalog; the builder loads them as empty lists. */
async function answerBuilderResources(page: Page): Promise<void> {
  await page.route(/\/api\/v1\/knowledgeBase\/\?/, (route) => route.fulfill({ json: { knowledgeBases: [] } }));
  await page.route(/\/api\/v1\/knowledgeBase\/knowledge-hub\/nodes/, (route) => route.fulfill({ json: { nodes: [], hasNext: false } }));
  await page.route(/\/api\/v1\/toolsets\/(my-toolsets|agents\/)/, (route) => route.fulfill({ json: { toolsets: [], pagination: { hasNext: false, page: 1, limit: 20, total: 0, totalPages: 1 } } }));
  await page.route(/\/api\/v1\/skills\?/, (route) => route.fulfill({ json: { skills: [] } }));
}

async function answerAgent(page: Page, overrides: Record<string, unknown> = {}): Promise<void> {
  await page.route(new RegExp(`/api/v1/agents/${AGENT_KEY}$`), async (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: agentBody(overrides) });
    return route.fulfill({
      status: 409,
      json: { error: { code: 'HANDLE_TAKEN', message: 'The handle @closers is already taken.', details: { suggestion: 'closers-2' } } },
    });
  });
}

async function stage(id: string, slug: string, run: () => Promise<void>, page?: Page): Promise<void> {
  try {
    await run();
  } catch (error) {
    const text = (error instanceof Error ? error.message : String(error)).replace(/\u001b\[[0-9;]*m/g, '');
    unreachable(id, slug, text.split('\n').slice(0, 3).join(' ').slice(0, 300));
    await page?.screenshot({ path: `${OUT_DIR}/failed-${id}-${slug}-${Date.now()}.png` }).catch(() => undefined);
  }
}

for (const combo of COMBOS) {
  test.describe(`${combo.viewport} ${combo.theme}`, () => {
    let cast: Cast;
    test.beforeEach(async ({ browser }) => {
      setCurrentCombo(combo);
      test.setTimeout(300_000);
      await fake.reset();
      cast = makeCast(browser, combo);
    });
    test.afterEach(async () => {
      await cast.close();
      await fake.reset();
    });

    test('the handle of an agent that predates handles', async () => {
      const alice = await cast.open('owner');
      const { page } = alice;
      page.on('response', (r) => { if (r.status() >= 400) console.log('HTTP', r.status(), r.url()); });
      await answerBuilderResources(page);
      await answerAgent(page, { handle: undefined });
      await stage('01', 'legacy-agent-derived-handle', async () => {
        await page.goto(`/agents/edit/?agentKey=${AGENT_KEY}`);
        const field = page.getByTestId('agent-handle-field');
        await expect(field.getByTestId('agent-handle-value')).toHaveText('@offer-drafter', { timeout: 60_000 });
        await shot(page, '01', 'legacy-agent-derived-handle', 'An agent saved before handles existed: the preview "@offer-drafter" (italic: derived from the name; saving after an edit stores it) with the edit pencil.', field);
      }, page);
      await stage('02', 'editing-handle', async () => {
        await page.getByRole('button', { name: 'Edit handle' }).click();
        await page.getByRole('textbox', { name: 'Agent handle' }).fill('offers');
        const field = page.getByTestId('agent-handle-field');
        await shot(page, '02', 'editing-handle', 'The inline editor open with "offers": an @ prefix slot, the input, an apply check and a cancel cross.', field);
      }, page);
      await stage('03', 'invalid-and-reserved', async () => {
        const input = page.getByRole('textbox', { name: 'Agent handle' });
        await input.fill('Bad Handle');
        const field = page.getByTestId('agent-handle-field');
        await expect(field.getByRole('alert')).toBeVisible();
        await shot(page, '03', 'invalid-handle', 'Typing "Bad Handle": the red message "Use 2-40 lowercase letters, digits or hyphens." and the apply button disabled.', field);
        await input.fill('assistant');
        await expect(field.getByRole('alert')).toContainText('reserved');
        await shot(page, '03b', 'reserved-handle', 'Typing "assistant": "@assistant is reserved. Choose another handle."', field);
      }, page);
    });

    test('the handle of a saved agent', async () => {
      const alice = await cast.open('owner');
      const { page } = alice;
      page.on('response', (r) => { if (r.status() >= 400) console.log('HTTP', r.status(), r.url()); });
      await stage('04', 'saved-agent-read-only', async () => {
        await answerBuilderResources(page);
        await answerAgent(page);
        await page.goto(`/agents/edit/?agentKey=${AGENT_KEY}`);
        const field = page.getByTestId('agent-handle-field');
        await expect(field.getByTestId('agent-handle-value')).toHaveText('@offer-drafter', { timeout: 60_000 });
        await shot(page, '04', 'saved-agent-handle', 'A saved agent the owner can edit: "Handle @offer-drafter" in monospace under the name, with the edit pencil beside it.', field);
      }, page);
      await stage('05', 'taken-handle-suggestion', async () => {
        const field = page.getByTestId('agent-handle-field');
        await field.getByRole('button', { name: 'Edit handle' }).click();
        await page.getByRole('textbox', { name: 'Agent handle' }).fill('closers');
        await page.getByRole('button', { name: 'Apply handle' }).click();
        await page.getByRole('button', { name: /save changes/i }).click();
        await expect(field.getByRole('alert')).toContainText('already taken', { timeout: 20_000 });
        await shot(page, '05', 'taken-handle-suggestion', 'Save answered 409 HANDLE_TAKEN: "@closers is already taken. Try @closers-2." with a "Use @closers-2" button.', field);
      }, page);
      await stage('06', 'read-only-viewer', async () => {
        await page.unroute(new RegExp(`/api/v1/agents/${AGENT_KEY}$`));
        await answerAgent(page, { can_edit: false, user_role: 'READER' });
        await page.goto(`/agents/edit/?agentKey=${AGENT_KEY}`);
        const field = page.getByTestId('agent-handle-field');
        await expect(field.getByTestId('agent-handle-value')).toHaveText('@offer-drafter', { timeout: 60_000 });
        await expect(field.getByRole('button', { name: 'Edit handle' })).toHaveCount(0);
        await shot(page, '06', 'viewer-no-edit', 'The same agent for someone who cannot edit it: the handle is plain text, no pencil.', field);
      }, page);
    });
  });
}

const DRAFT = {
  draftId: '5d1f6d1e-1a52-4f67-a3d4-6a4a1f3a9a01',
  name: 'Offer drafter',
  handleSuggestion: 'offer-drafter',
  description: 'Drafts offer letters from our price lists and approved templates.',
  instructions: 'Write short, friendly offer letters. Always cite the price list you used and never promise a discount.',
  knowledge: ['connector-sales-drive', 'kb-price-lists'],
  toolsets: [],
  suggestedTools: ['jira__create_issue', 'gmail__send_email'],
  provenance: 'sender',
};

async function draftedChat(provenance: 'sender' | 'content' = 'sender'): Promise<string> {
  const { roster } = stackState();
  const owner = new NodeApi(roster.owner);
  await fake.reset();
  await fake.script('chat_stream', { kind: 'agent_draft', draft: { ...DRAFT, provenance, requestedBy: roster.owner.userId } });
  const chat = await owner.startChat('Make me an agent that drafts offer letters from our price lists');
  expect((await owner.share(chat, roster.write_recipient, 'write')).status).toBe(200);
  return chat;
}

async function openDrafted(page: Page, chat: string): Promise<void> {
  await page.goto(`/chat/?conversationId=${chat}`);
  await expect(page.getByTestId('agent-draft-card').or(page.getByTestId('agent-draft-redacted'))).toBeVisible({ timeout: 60_000 });
}

for (const combo of COMBOS) {
  test.describe(`draft card ${combo.viewport} ${combo.theme}`, () => {
    let cast: Cast;
    test.beforeEach(async ({ browser }) => {
      setCurrentCombo(combo);
      test.setTimeout(300_000);
      cast = makeCast(browser, combo);
    });
    test.afterEach(async () => {
      await cast.close();
      await fake.reset();
    });

    test('the requester reviews, hits a taken handle, creates; a colleague sees a notice', async () => {
      const chat = await draftedChat('sender');
      const alice = await cast.open('owner');
      const { page } = alice;
      await stage('07', 'draft-card-sender', async () => {
        await openDrafted(page, chat);
        const card = page.getByTestId('agent-draft-card');
        await expect(card.getByTestId('agent-draft-handle-status')).toBeVisible({ timeout: 20_000 });
        // Tall enough that the element shot holds the whole card instead of what the chat column shows.
        await page.setViewportSize({ width: combo.size.width, height: 1500 });
        await shot(page, '07', 'draft-card-sender', 'The requester\'s draft card: editable name, @handle with its "Available" check, description, instructions, the two proposed knowledge sources ticked, the two suggested tools unticked and marked "Not set up for you", and "Private - only you can use it" beside Create.', card);
      }, page);
      await stage('08', 'handle-taken', async () => {
        await fake.script('agent_create_from_chat', {
          kind: 'reply',
          status: 409,
          body: { detail: { code: 'HANDLE_TAKEN', message: 'The handle @offer-drafter is already taken.', suggestion: 'offer-drafter-2' } },
        });
        const card = page.getByTestId('agent-draft-card');
        await card.getByTestId('agent-draft-create').click();
        await expect(card.getByTestId('agent-draft-use-suggestion')).toBeVisible({ timeout: 20_000 });
        await shot(page, '08', 'handle-taken', 'Create answered 409: the handle shows "@offer-drafter is already taken" in red with a "Use @offer-drafter-2" button, only inline (no second toast); the card stays editable.', card);
      }, page);
      await stage('09', 'created-toast', async () => {
        const card = page.getByTestId('agent-draft-card');
        await card.getByTestId('agent-draft-use-suggestion').click();
        await card.getByTestId('agent-draft-create').click();
        await expect(page.getByTestId('agent-draft-created')).toBeVisible({ timeout: 20_000 });
        await expect(page.getByRole('button', { name: 'Open agent chat' }).first()).toBeVisible({ timeout: 10_000 });
        await shot(page, '09', 'created-toast', 'After Create: the card collapses to "Created @offer-drafter-2 (private)" with an "Open agent chat" button, and a toast offers "Open agent chat" (no "Mention here" until M2).', page.getByTestId('agent-draft-card'));
      }, page);
      await stage('10', 'redacted-view', async () => {
        const bob = await cast.open('write_recipient');
        await openDrafted(bob.page, chat);
        await expect(bob.page.getByTestId('agent-draft-redacted')).toBeVisible();
        await shot(bob.page, '10', 'redacted-view', 'A colleague in the shared chat: one line, "<name> drafted an agent", with none of the draft\'s name, sources or instructions.', bob.page.getByTestId('agent-draft-redacted'));
      }, page);
    });

    test('a draft shaped by document content', async () => {
      const chat = await draftedChat('content');
      const alice = await cast.open('owner');
      await stage('11', 'draft-card-content', async () => {
        await openDrafted(alice.page, chat);
        const card = alice.page.getByTestId('agent-draft-card');
        await expect(card.getByTestId('agent-draft-content-banner')).toBeVisible();
        await alice.page.setViewportSize({ width: combo.size.width, height: 1500 });
        await shot(alice.page, '11', 'draft-card-content', 'Provenance "content": the amber "Suggested from document content" banner on top and nothing pre-ticked.', card);
      }, alice.page);
    });
  });
}

const JIRA_TOOLSET = {
  name: 'jira', normalized_name: 'jira', displayName: 'Jira', description: 'Jira', iconPath: '', category: 'app', toolCount: 1,
  tools: [{ name: 'create_issue', fullName: 'jira.create_issue', description: 'Create an issue' }],
  isConfigured: true, isAuthenticated: true, isFromRegistry: false, instanceId: 'inst-jira', instanceName: 'Main Jira', toolsetType: 'jira',
};

/** Names for the draft's knowledge ids and one signed-in Jira instance, as the builder APIs would answer them. */
async function answerCardLookups(page: Page): Promise<void> {
  await page.route(/\/api\/v1\/knowledgeBase\/knowledge-hub\/nodes/, (route) =>
    route.fulfill({ json: { nodes: [{ id: 'connector-sales-drive', name: 'Sales Drive' }], hasNext: false } }));
  await page.route(/\/api\/v1\/knowledgeBase\/?\?/, (route) =>
    route.fulfill({ json: { knowledgeBases: [{ id: 'kb-price-lists', connectorId: 'kb-price-lists', name: 'Price lists' }] } }));
  await page.route(/\/api\/v1\/toolsets\/my-toolsets/, (route) =>
    route.fulfill({ json: { toolsets: [JIRA_TOOLSET], pagination: { hasNext: false, page: 1, limit: 20, total: 1, totalPages: 1 } } }));
}

const HANDLE_CHECK = /\/api\/v1\/agents\/handle-availability/;
const CREATE = /\/api\/v1\/agents\/create/;

const accessError = (code: string, message: string, ids: string[]) => ({
  kind: 'reply' as const, status: 400, body: { detail: { code, message, ids } },
});

for (const combo of COMBOS) {
  test.describe(`draft card states ${combo.viewport} ${combo.theme}`, () => {
    let cast: Cast;
    test.beforeEach(async ({ browser }) => {
      setCurrentCombo(combo);
      test.setTimeout(300_000);
      cast = makeCast(browser, combo);
    });
    test.afterEach(async () => {
      await cast.close();
      await fake.reset();
    });

    test('the @ picker offers no agent in a plain chat (RR #16a)', async () => {
      const { roster } = stackState();
      const agents = [
        { _key: 'agent-own-1', name: 'Offer drafter', handle: 'offer-drafter', createdBy: roster.owner.userId, isServiceAccount: false },
        { _key: 'agent-own-2', name: 'Weekly digest', handle: 'weekly-digest', createdBy: roster.owner.userId, isServiceAccount: false },
        { _key: 'agent-colleague', name: 'Colleague private agent', handle: 'colleague-bot', createdBy: roster.write_recipient.userId, isServiceAccount: false },
      ];
      const listing = { kind: 'reply' as const, body: { success: true, agents, pagination: { currentPage: 1, limit: 100, totalItems: 3, totalPages: 1 } } };
      await fake.script('agent_list', ...Array.from({ length: 40 }, () => listing));
      const { sessionId } = await fake.seedChat({ key: `vph11-picker-${Date.now().toString(36)}`, users: { write_recipient: 'write' } });
      const alice = await cast.open('owner');
      const { page } = alice;
      await stage('16', 'picker-no-agents-plain-chat', async () => {
        const list = page.getByRole('listbox', { name: 'Mention suggestions' });
        await page.goto(`/chat/?conversationId=${sessionId}`);
        await expect(composer(page)).toBeVisible({ timeout: 30_000 });
        await composer(page).click();
        await page.keyboard.type('@');
        await expect(list.getByRole('option').first()).toBeVisible({ timeout: 20_000 });
        for (const name of ['Offer drafter', 'Weekly digest', 'Colleague private agent']) {
          await expect(list.getByRole('option').filter({ hasText: name })).toHaveCount(0);
        }
        await shot(page, '16', 'picker-no-agents-plain-chat', 'Owner types @ in a shared chat that has no agent: Assistant and people only. The picker offers exactly what the server accepts (RR #16a), so her own agents are not listed here.');
        await page.keyboard.press('Escape');
      }, page);
    });

    test('handle states, creating, refusals, builder off', async () => {
      const chat = await draftedChat('sender');
      const alice = await cast.open('owner');
      const { page } = alice;
      await answerCardLookups(page);
      const card = page.getByTestId('agent-draft-card');
      const handle = card.getByTestId('agent-draft-handle');
      const tall = () => page.setViewportSize({ width: combo.size.width, height: 1500 });
      await stage('12', 'card-handle-checking', async () => {
        let release: () => void = () => undefined;
        const held = new Promise<void>((r) => { release = r; });
        await page.route(HANDLE_CHECK, async (route) => { await held; await route.fulfill({ json: { available: true } }); });
        await openDrafted(page, chat);
        await tall();
        await expect(card.getByTestId('agent-draft-handle-status')).toContainText(/checking/i, { timeout: 20_000 });
        await shot(page, '12', 'card-handle-checking', 'While the handle is being checked: a grey "Checking @offer-drafter…" line under the field; knowledge shows names (Sales Drive, Price lists) and the Jira tool is tickable.', card);
        release();
        await page.unroute(HANDLE_CHECK);
      }, page);
      await stage('12b', 'card-handle-taken-precheck', async () => {
        await page.route(HANDLE_CHECK, (route) =>
          route.fulfill({ json: { available: false, reason: 'taken', suggestion: 'offers-2' } }));
        try {
          await handle.fill('offers');
          await expect(card.getByTestId('agent-draft-use-suggestion')).toBeVisible({ timeout: 20_000 });
          await shot(page, '12b', 'card-handle-taken-precheck', 'Live check says taken: red "@offers is already taken", a "Use @offers-2" button, Create disabled.', card);
        } finally {
          await page.unroute(HANDLE_CHECK);
        }
      }, page);
      await stage('12c', 'card-handle-reserved', async () => {
        await handle.fill('assistant');
        await expect(card.getByTestId('agent-draft-handle-status')).toContainText(/reserved/i);
        await shot(page, '12c', 'card-handle-reserved', 'Typing "assistant": red "@assistant is reserved", Create disabled.', card);
      }, page);
      await stage('12d', 'card-handle-invalid', async () => {
        await handle.fill('Bad Handle');
        await expect(card.getByTestId('agent-draft-handle-status')).toBeVisible();
        await shot(page, '12d', 'card-handle-invalid', 'Typing "Bad Handle": red format hint, Create disabled.', card);
        await handle.fill('offer-drafter');
        await expect(card.getByTestId('agent-draft-handle-status')).toContainText(/available/i, { timeout: 20_000 });
      }, page);
      await stage('13', 'card-creating', async () => {
        let release: () => void = () => undefined;
        const held = new Promise<void>((r) => { release = r; });
        await page.route(CREATE, async (route) => { await held; await route.fulfill({ status: 500, json: { error: { code: 'INTERNAL', message: 'boom' } } }); });
        await card.getByTestId('agent-draft-tool-jira__create_issue').click();
        await card.getByTestId('agent-draft-create').click();
        await expect(card.getByTestId('agent-draft-create')).toBeDisabled();
        await shot(page, '13', 'card-creating', 'Create in flight: the button shows a spinner and is disabled; the Jira tool the user ticked stays ticked.', card);
        release();
        await expect(card.getByTestId('agent-draft-error')).toBeVisible({ timeout: 20_000 });
        await shot(page, '14', 'card-error-generic', 'A server failure: a red "couldn\'t create" line above the footer, no global toast, the card still editable.', card);
        await page.unroute(CREATE);
      }, page);
      await stage('14b', 'card-invalid-knowledge', async () => {
        await fake.script('agent_create_from_chat', accessError('INVALID_KNOWLEDGE', 'Some knowledge sources are not available to you.', ['kb-price-lists']));
        await card.getByTestId('agent-draft-create').click();
        await expect(card.getByTestId('agent-draft-knowledge-kb-price-lists')).toBeDisabled({ timeout: 20_000 });
        await shot(page, '14b', 'card-invalid-knowledge', 'INVALID_KNOWLEDGE for Price lists: that box is unticked and disabled with a red "No longer available to you"; Sales Drive stays ticked.', card);
      }, page);
      await stage('14c', 'card-invalid-toolset', async () => {
        await fake.script('agent_create_from_chat', accessError('INVALID_TOOLSET', 'Some tools are not set up and signed in for you.', ['inst-jira']));
        await card.getByTestId('agent-draft-create').click();
        await expect(card.getByTestId('agent-draft-tool-jira__create_issue')).toBeDisabled({ timeout: 20_000 });
        await shot(page, '14c', 'card-invalid-toolset', 'INVALID_TOOLSET for the Jira instance: the Jira tool is unticked and disabled with "No longer available to you".', card);
      }, page);
      await stage('15', 'card-builder-off', async () => {
        await fake.setFlag(false, 'ENABLE_CHAT_AGENT_BUILDER');
        try {
          await openDrafted(page, chat);
          await expect(card).toHaveAttribute('data-state', 'off', { timeout: 20_000 });
          await tall();
          await shot(page, '15', 'card-builder-off', 'Builder turned off after the draft: every field read-only, no Create button, footer "Agent builder is turned off".', card);
        } finally {
          await fake.setFlag(true, 'ENABLE_CHAT_AGENT_BUILDER');
        }
      }, page);
    });

    test('the agent builder page as a whole', async () => {
      const alice = await cast.open('owner');
      const { page } = alice;
      await answerBuilderResources(page);
      await answerAgent(page);
      await stage('17', 'builder-page', async () => {
        await page.goto(`/agents/edit/?agentKey=${AGENT_KEY}`);
        await expect(page.getByTestId('agent-handle-field').getByTestId('agent-handle-value')).toHaveText('@offer-drafter', { timeout: 60_000 });
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        await shot(page, '17', 'builder-page', `The whole builder page with the handle under the name; horizontal overflow ${overflow}px (0 expected); Save visible in the header.`);
      }, page);
    });
  });
}

test('zz write INDEX.md', () => {
  writeIndex();
});
