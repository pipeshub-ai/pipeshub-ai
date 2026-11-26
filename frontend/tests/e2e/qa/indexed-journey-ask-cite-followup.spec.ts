/**
 * QA-CLASSIC journey J1 (was QA-AUTO's new-interface one): ask → cite → follow-up, on records the stack really
 * indexed (the QA stack runs indexing; project `qa-indexed`). A record is uploaded to a collection and indexed; an
 * agent reads that collection; a question is answered from it with a citation, and the answer's Citation tab names
 * the record; a follow-up in the same chat is answered there; and a reload keeps both turns with the first one's
 * citation. The scripted model searches and cites (`[s](ref1)`), so nothing reaches a paid model; the retrieval,
 * citation mapping and UI are the product's.
 */
import { randomBytes } from 'crypto';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { cancelLeftRuns, script } from '../harness/support/lane';
import { createTestKb, deleteTestKb, uploadFileByApi, waitForIndexed } from '../knowledge-base/kb-upload.helpers';
import { answerActions, answerText, composer, sendInNewChat } from './classic';

test.describe.configure({ mode: 'serial' });

const MARKER = `QAJ1${randomBytes(3).toString('hex')}`;
const RECORD_NAME = `ledger-${MARKER}`;
const RECORD = `# Quarterly ledger\n\nThe quarterly ledger of the regions, ${MARKER}: the south region closed at 800 units.\n`;
const QUERY = 'quarterly ledger regions';
// The chat loop's search tool, or the knowledge MCP's where the agent's tools come from it (as KN-FS words it).
const SEARCH = `tool search_internal_knowledge ${JSON.stringify({ query: QUERY })} || knowledgegraph__search ${JSON.stringify({ query: QUERY })}`;

let kbId = '';
let agentKey = '';

test.afterEach(async ({ apiContext }) => {
  await cancelLeftRuns(apiContext);
  if (agentKey) await apiContext.delete(`/api/v1/agents/${agentKey}`);
  if (kbId) await deleteTestKb(apiContext, kbId);
});

/** The answer's Citation tab, by its role (QA-CLASSIC-FU-1): selecting it says so. */
async function openCitations(page: Page): Promise<void> {
  const citationTab = page.getByRole('tab', { name: /^Citation/ }).first();
  await citationTab.click();
  await expect(citationTab).toHaveAttribute('aria-selected', 'true');
}

test('QA-J1: an answer cites an indexed record, its Citation tab names it, and a follow-up keeps the thread', async ({ page, apiContext }) => {
  test.setTimeout(600_000);
  kbId = (await createTestKb(apiContext, `QA journey ${MARKER}`)).id;
  const recordId = await uploadFileByApi(kbId, { name: `${RECORD_NAME}.md`, mimeType: 'text/markdown', buffer: Buffer.from(RECORD) });
  await waitForIndexed(apiContext, recordId);

  const created = await apiContext.post('/api/v1/agents/create', {
    data: { name: `Ledger reader ${MARKER}`, description: 'Reads the ledger.', systemPrompt: 'You are a careful analyst.', shareWithOrg: false },
  });
  expect(created.ok(), `creating the agent: ${created.status()}`).toBe(true);
  const body = (await created.json()) as { agent?: { _key?: string }; _key?: string };
  agentKey = body.agent?._key ?? body._key ?? '';
  const knowledge = await apiContext.put(`/api/v1/agents/${agentKey}`, { data: { knowledge: [{ connectorId: kbId }] } });
  expect(knowledge.ok(), `giving the agent the collection: ${knowledge.status()}`).toBe(true);

  const first = 'The south region closed at 800 units';
  const conversationId = await sendInNewChat(page, script('How did the south region close?', SEARCH, `say ${first} [s](ref1).`), agentKey);
  await expect(page.getByText(first).first()).toBeVisible({ timeout: 120_000 });
  await expect(answerActions(page).last()).toBeVisible({ timeout: 60_000 });

  // Cite: the answer's Citation tab lists the record it came from.
  await openCitations(page);
  await expect(page.getByText(RECORD_NAME).first(), 'the indexed record is cited').toBeVisible({ timeout: 30_000 });

  // Follow-up in the same chat.
  const second = 'Every quarter, the ledger closes on its last day.';
  await composer(page).fill(script('When does the ledger close?', `say ${second}`));
  await composer(page).press('Enter');
  await expect(answerText(page, second)).toBeVisible({ timeout: 90_000 });
  expect(new URL(page.url()).searchParams.get('conversationId'), 'the follow-up stays in the thread').toBe(conversationId);
  await expect(page.getByText(first).first(), 'the first answer stays in the thread').toBeVisible();

  // A reload keeps both turns, and the first one's citation.
  await page.reload();
  await expect(answerText(page, second)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(first).first()).toBeVisible();
  await openCitations(page);
  await expect(page.getByText(RECORD_NAME).first()).toBeVisible({ timeout: 30_000 });
});
