import { test, expect, type Page } from '@playwright/test';
import { NodeApi, fake, stackState, waitFor } from './support/stack';
import { COMBOS, VISUAL_ENABLED, makeCast, setCurrentCombo, shot, unreachable, type Cast } from './support/visual-ph11';

/**
 * Agent builder v2 card review (resolved knowledge / actions / web search / what could not be added), desktop and mobile, light and dark.
 * Opt-in: `PCC_VISUAL_TOUR=1 tests/e2e/collab-chats/run.sh visual-agent-builder`. Files land in `test-results/visual-ph11/` as `ab-*`.
 */
test.skip(!VISUAL_ENABLED, 'agent builder visual review: set PCC_VISUAL_TOUR=1');
test.describe.configure({ mode: 'serial' });

const JIRA_TOOLS = ['create_issue', 'add_comment', 'search_issues', 'get_issue', 'update_issue', 'transition_issue', 'assign_issue'];
const tool = (set: string, name: string) => ({ name, fullName: `${set}.${name}`, description: `${name.replace(/_/g, ' ')}` });

const V2 = {
  draftId: '7c2a9e44-0b11-4a5e-9b0e-2f6d1c0a7a01',
  name: 'HR helper',
  handleSuggestion: 'hr-helper',
  description: 'Answers HR questions from our policies, files Jira tickets for follow-ups and posts updates in Slack.',
  instructions: 'Answer from the HR Policies collection and cite the policy. Open a Jira issue when an employee needs a follow-up.',
  knowledge: ['kb-hr', 'connector-gdrive'],
  knowledgeSources: [
    { id: 'kb-hr', name: 'HR Policies', kind: 'collection', connectorType: null },
    { id: 'connector-gdrive', name: 'Google Drive', kind: 'connector', connectorType: 'GOOGLE_DRIVE' },
  ],
  actions: [
    { instanceId: 'inst-jira', instanceName: 'Jira', name: 'jira', displayName: 'Jira', iconPath: '', category: 'app', tools: JIRA_TOOLS.map((n) => tool('jira', n)) },
    { instanceId: 'inst-slack', instanceName: 'Slack', name: 'slack', displayName: 'Slack', iconPath: '', category: 'app', tools: [tool('slack', 'send_message')] },
  ],
  webSearch: { provider: 'duckduckgo', providerLabel: 'DuckDuckGo' },
  unresolved: [
    { kind: 'knowledge', query: 'Payroll', reason: 'not_found', candidates: [] },
    { kind: 'tool', query: 'Salesforce', reason: 'not_connected', candidates: [] },
    { kind: 'knowledge', query: 'Handbook', reason: 'ambiguous', candidates: ['Employee Handbook', 'Handbook 2023'] },
  ],
  toolsets: [],
  suggestedTools: [],
  provenance: 'sender',
};

const LEGACY = {
  draftId: '1e0f6d1e-1a52-4f67-a3d4-6a4a1f3a9a09',
  name: 'Offer drafter',
  handleSuggestion: 'offer-drafter',
  description: 'Drafts offer letters from our price lists.',
  instructions: 'Write short, friendly offer letters.',
  knowledge: ['kb-price-lists'],
  toolsets: [],
  suggestedTools: ['jira__create_issue'],
  provenance: 'sender',
};

async function answerPickerData(page: Page): Promise<void> {
  const jira = {
    name: 'jira', normalized_name: 'jira', displayName: 'Jira', description: '', iconPath: '', category: 'app', toolCount: 2, isConfigured: true, isAuthenticated: true, isFromRegistry: false,
    instanceId: 'inst-jira', instanceName: 'Jira', toolsetType: 'jira', tools: [tool('jira', 'create_issue'), tool('jira', 'get_issue')],
  };
  const github = { ...jira, name: 'github', normalized_name: 'github', displayName: 'GitHub', instanceId: 'inst-gh', toolsetType: 'github', instanceName: 'Engineering GitHub', tools: [tool('github', 'create_pull_request'), tool('github', 'list_issues'), tool('github', 'comment_on_pull_request')] };
  const confluence = { ...jira, name: 'confluence', normalized_name: 'confluence', displayName: 'Confluence', instanceId: 'inst-conf', toolsetType: 'confluence', instanceName: 'Confluence', tools: [tool('confluence', 'create_page')] };
  await page.route(/\/api\/v1\/toolsets\/my-toolsets/, (route) =>
    route.fulfill({ json: { toolsets: [jira, github, confluence], pagination: { hasNext: false, page: 1, limit: 20, total: 3, totalPages: 1 } } }),
  );
  await page.route(/\/api\/v1\/knowledgeBase\/\?/, (route) =>
    route.fulfill({ json: { knowledgeBases: [{ id: 'kb-hr', connectorId: 'kb-hr', name: 'HR Policies' }, { id: 'kb-price-lists', connectorId: 'kb-price-lists', name: 'Price lists' }, { id: 'kb-eng', connectorId: 'kb-eng', name: 'Engineering Runbooks' }] } }),
  );
  await page.route(/\/api\/v1\/knowledgeBase\/knowledge-hub\/nodes/, (route) =>
    route.fulfill({
      json: {
        items: [
          { id: 'connector-gdrive', name: 'Google Drive', nodeType: 'app', parentId: null, origin: 'CONNECTOR', connector: 'GOOGLE_DRIVE', recordType: null, recordGroupType: null, indexingStatus: null, reason: null, createdAt: 1, updatedAt: 1 },
          { id: 'connector-slack', name: 'Slack', nodeType: 'app', parentId: null, origin: 'CONNECTOR', connector: 'SLACK', recordType: null, recordGroupType: null, indexingStatus: null, reason: null, createdAt: 1, updatedAt: 1 },
        ],
        pagination: { hasNext: false },
      },
    }),
  );
}

async function drafted(draft: Record<string, unknown>, provenance: 'sender' | 'content' = 'sender'): Promise<string> {
  const { roster } = stackState();
  const owner = new NodeApi(roster.owner);
  await fake.reset();
  await fake.script('chat_stream', { kind: 'agent_draft', draft: { ...draft, provenance, requestedBy: roster.owner.userId } });
  return owner.startChat('Make me an HR agent that uses HR Policies, Jira and web search');
}

async function open(page: Page, chat: string, combo: { size: { width: number; height: number } }): Promise<void> {
  await page.goto(`/chat/?conversationId=${chat}`);
  await expect(page.getByTestId('agent-draft-card').first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('agent-draft-create').first()).toBeEnabled({ timeout: 30_000 });
  await page.setViewportSize({ width: combo.size.width, height: 2400 });
}

async function noSideScroll(page: Page): Promise<void> {
  const over = await page.evaluate(() => {
    const card = document.querySelector('[data-testid="agent-draft-card"]') as HTMLElement | null;
    return { page: document.documentElement.scrollWidth - document.documentElement.clientWidth, card: card ? card.scrollWidth - card.clientWidth : 0 };
  });
  expect(over.page, 'page scrolls sideways').toBeLessThanOrEqual(1);
  expect(over.card, 'card overflows its box').toBeLessThanOrEqual(1);
}

async function stage(id: string, slug: string, run: () => Promise<void>, page?: Page): Promise<void> {
  try {
    await run();
  } catch (error) {
    const text = (error instanceof Error ? error.message : String(error)).replace(/\u001b\[[0-9;]*m/g, '');
    unreachable(id, slug, text.split('\n').slice(0, 3).join(' ').slice(0, 300));
    await page?.screenshot({ path: `test-results/visual-ph11/failed-${id}-${slug}-${Date.now()}.png` }).catch(() => undefined);
  }
}

for (const combo of COMBOS) {
  test.describe(`agent builder card ${combo.viewport} ${combo.theme}`, () => {
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

    test('fresh card, Add pickers, created', async () => {
      const chat = await drafted(V2);
      const { page } = await cast.open('owner');
      await answerPickerData(page);
      const card = page.getByTestId('agent-draft-card');
      await stage('ab-01', 'fresh-sender', async () => {
        await open(page, chat, combo);
        await expect(card.getByTestId('agent-draft-summary')).toHaveText('2 knowledge sources · 8 actions · Web search (DuckDuckGo)');
        await noSideScroll(page);
        await shot(page, 'ab-01', 'fresh-sender', 'HR helper draft from the requester: knowledge, Jira (collapsed to 5 of 7 with "Show all") and Slack actions all ticked, web search on, the three things that could not be added, the summary line and Create.', card);
        await card.getByTestId('agent-draft-toolset-more-inst-jira').click();
        await shot(page, 'ab-01b', 'jira-expanded', 'The same card with Jira expanded to all seven actions.', card);
        await card.getByTestId('agent-draft-toolset-more-inst-jira').click();
      }, page);
      await stage('ab-03', 'add-actions-picker', async () => {
        await page.setViewportSize(combo.size);
        await card.getByTestId('agent-draft-add-actions').scrollIntoViewIfNeeded();
        await card.getByTestId('agent-draft-add-actions').click();
        await expect(page.getByTestId('agent-draft-picker')).toBeVisible();
        await expect(page.getByTestId('agent-draft-picker-tool-github.create_pull_request')).toBeVisible({ timeout: 20_000 });
        await shot(page, 'ab-03', 'add-actions-picker', 'Add actions open at the real viewport height: search box, Jira already on the card is not offered again, GitHub and Confluence are, Add button reachable.');
        await page.getByTestId('agent-draft-picker-search').fill('pull');
        await page.getByTestId('agent-draft-picker-tool-github.create_pull_request').click();
        await shot(page, 'ab-03b', 'add-actions-picker-filtered-ticked', 'Searched for "pull" and ticked one tool: the Add button counts it.');
        await page.getByTestId('agent-draft-picker-add').click();
        await expect(card.getByTestId('agent-draft-tool-github.create_pull_request')).toBeChecked();
      }, page);
      await stage('ab-03c', 'add-knowledge-picker', async () => {
        await card.getByTestId('agent-draft-add-knowledge').scrollIntoViewIfNeeded();
        await card.getByTestId('agent-draft-add-knowledge').click();
        await expect(page.getByTestId('agent-draft-picker-knowledge-connector-slack')).toBeVisible({ timeout: 20_000 });
        await shot(page, 'ab-03c', 'add-knowledge-picker', 'Add knowledge open: collections and connectors not already on the card, with icons.');
        await page.keyboard.press('Escape');
      }, page);
      await page.keyboard.press('Escape');
      await stage('ab-04', 'created', async () => {
        const mark = await fake.mark();
        await page.setViewportSize({ width: combo.size.width, height: 1500 });
        await card.getByTestId('agent-draft-create').click();
        await expect(page.getByTestId('agent-draft-created')).toBeVisible({ timeout: 20_000 });
        const [sent] = await waitFor('the create to reach Python', async () => {
          const rows = await fake.requests(['agent_create_from_chat'], mark);
          return rows.length ? rows : false;
        });
        const body = sent.body as Record<string, any>;
        expect(body.knowledge.map((k: any) => k.connectorId).sort()).toEqual(['connector-gdrive', 'kb-hr']);
        expect(body.toolsets.map((t: any) => t.instanceId).sort()).toEqual(['inst-gh', 'inst-jira', 'inst-slack']);
        expect(body.webSearch.provider).toBe('duckduckgo');
        await shot(page, 'ab-04', 'created', 'After Create: "Created @hr-helper (private)", the one-line list of what was attached, Open agent chat and Edit agent.', card);
      }, page);
    });

    test('content provenance: nothing ticked, banner', async () => {
      const chat = await drafted(V2, 'content');
      const { page } = await cast.open('owner');
      await stage('ab-02', 'content-provenance', async () => {
        await open(page, chat, combo);
        const card = page.getByTestId('agent-draft-card');
        await expect(card.getByTestId('agent-draft-content-banner')).toBeVisible();
        await expect(card.getByRole('checkbox', { checked: true })).toHaveCount(0);
        await expect(card.getByTestId('agent-draft-summary')).toBeVisible();
        await noSideScroll(page);
        await shot(page, 'ab-02', 'content-provenance', 'The draft was shaped by document content: the amber banner, every box and the web search switch off, the summary says nothing is attached.', card);
      }, page);
    });

    test('superseded card above a newer draft', async () => {
      const chat = await drafted({ ...V2, name: 'HR helper (first)' });
      const { page } = await cast.open('owner');
      const { roster } = stackState();
      await stage('ab-05', 'superseded', async () => {
        await open(page, chat, combo);
        await fake.script('chat_stream', {
          kind: 'agent_draft',
          draft: { ...V2, draftId: '7c2a9e44-0b11-4a5e-9b0e-2f6d1c0a7a02', name: 'HR helper', revisesDraftId: V2.draftId, provenance: 'sender', requestedBy: roster.owner.userId },
        });
        await page.setViewportSize({ width: combo.size.width, height: 900 });
        await page.getByTestId('chat-composer').fill('Also add Slack');
        await page.getByRole('button', { name: 'Send message' }).click();
        await expect(page.getByTestId('agent-draft-superseded')).toBeVisible({ timeout: 40_000 });
        await expect(page.getByTestId('agent-draft-card')).toHaveCount(1);
        await page.setViewportSize({ width: combo.size.width, height: 2400 });
        await noSideScroll(page);
        await shot(page, 'ab-05', 'superseded', 'The first draft collapsed to "Updated in a newer draft below" with a Show button, the newer card live underneath.');
        await page.getByTestId('agent-draft-superseded').getByRole('button').click();
        await shot(page, 'ab-05b', 'superseded-expanded', 'The superseded card expanded: read-only, no Create, a Hide button.');
      }, page);
    });

    test('legacy draft still renders', async () => {
      const chat = await drafted(LEGACY);
      const { page } = await cast.open('owner');
      await answerPickerData(page);
      await stage('ab-06', 'legacy-draft', async () => {
        await open(page, chat, combo);
        const card = page.getByTestId('agent-draft-card');
        await expect(card.getByTestId('agent-draft-name')).toHaveValue('Offer drafter');
        await noSideScroll(page);
        await shot(page, 'ab-06', 'legacy-draft', 'A draft stored before v2 (bare knowledge id, suggested tool names, no resolved sections): still a usable card, names looked up for the knowledge id, tool hint ticked on request.', card);
      }, page);
    });
  });
}
