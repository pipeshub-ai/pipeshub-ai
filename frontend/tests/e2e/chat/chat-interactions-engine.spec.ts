/**
 * chat-interactions-engine.spec.ts — UXINT-1/3 (gallery §10), BGUI-4: an engine child's approval floats over the
 * message box (compact since BGUI-9, the whole card under Details), and its job card, the one row the child gets, named by its goal (CLASSIC-JOBS D2; no separate "Code
 * task: …" step), keeps a pointer to it until it is answered. The card says
 * which engine asks ("Codex wants to:", from the child's `childEngine`) and is worded like any other card ("Create
 * ticket in e2esaas?", never "pipeshub-subagent" or the raw tool id).
 */
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from './agui-sse-builder';
import { evidenceFonts } from './uxint-replay';
import { FakeRunStream, json, MODEL_INFO, mockInFlightConversation, openConversation, type SseFrame } from './durable-conversation.mock';

const SHOTS = process.env.UXINT_SHOTS;
async function shot(page: Page, name: string) {
  await page.waitForTimeout(300);
  await page.screenshot({ path: test.info().outputPath(`${name}.png`) });
  if (SHOTS) {
    // Evidence only: the dev server's own overlay is not part of the page, and icons need their font.
    await page.addStyleTag({ content: 'nextjs-portal { display: none !important; }' });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `${SHOTS}/${name}.png` });
  }
}

const SESSION = 'conv-uxintc-engine';
const RUN = 'run-uxintc-engine';
const CHILD = 'run-uxintc-engine-child';
const IX = `ix.approve.k${'1'.repeat(40)}`;

test('Chat — a Codex child’s card floats over the message box and names Codex (UXINT-1, UXINT-3, BGUI-4)', async ({ page }) => {
  const specs: Array<[string, Record<string, unknown>]> = [
    ['RUN_STARTED', { threadId: SESSION, runId: RUN }],
    ['TEXT_MESSAGE_START', { messageId: 'm1', role: 'assistant', runId: RUN }],
    ['TEXT_MESSAGE_CONTENT', { messageId: 'm1', delta: 'Handing this to the coding engine.', runId: RUN }],
    ['TEXT_MESSAGE_END', { messageId: 'm1', runId: RUN }],
    ['STEP_STARTED', { stepName: 'sub_agent:pipeshub-subagent', runId: RUN }],
    ['RUN_STARTED', { threadId: SESSION, runId: CHILD, parentRunId: RUN, codeTaskGoal: 'File the wonders ticket' }],
    [
      'CUSTOM',
      {
        name: 'approval_requested',
        value: {
          interactionId: IX,
          toolCallId: IX,
          toolName: 'mcp_e2esaas_create_ticket',
          args: JSON.stringify({ title: 'uxintc s8', body: 'from Codex' }),
          argsSummary: null,
          reason: { rule: 'engine_writes', layer: 'engine', sensitivity: 'public', sources: [], moreSources: 0, conversationGrant: false },
          seq: 9,
          afterTurns: 0,
          childRunId: CHILD,
          childRole: 'pipeshub-subagent',
          childEngine: 'codex',
        },
        runId: RUN,
      },
    ],
    // Not released: the run is still going, so the page reattaches to it.
    ['STATE_SNAPSHOT', { runId: RUN, snapshot: { later: true } }],
  ];
  const frames: SseFrame[] = specs.map(([type, data], i) => ({ id: `j:${i + 1}.1/0.0.0`, type, data }));
  const stream = new FakeRunStream(() => frames);
  stream.released = frames.length - 1;
  await mockInFlightConversation(page, {
    sessionId: SESSION,
    runId: RUN,
    stream,
    stored: () => buildAguiConversation({ conversationId: SESSION, userMessageId: 'u1', question: 'uxintc-s8 engine ticket', modelInfo: MODEL_INFO }),
  });
  await page.route(`**/api/v1/conversations/${SESSION}/runs/${RUN}/interactions/authority`, (route) => json(route, { approve: true, deny: true }));
  await evidenceFonts(page);
  await openConversation(page, SESSION);

  // BGUI-4, BGUI-9: the ask floats over the message box as the design's compact card, saying which job needs you
  // and the action in the card's own words; the job card, named by its goal and waiting on the person, holds a
  // pointer to it, with no second set of buttons and no "Code task: …" row.
  const float = page.getByTestId('job-ask-float');
  await expect(float).toBeVisible({ timeout: 30_000 });
  await expect(float.getByTestId('job-ask-headline')).toHaveText('File the wonders ticket is asking first');
  await expect(float.getByTestId('job-ask-action')).toHaveText('Create ticket in e2esaas?');
  await expect(float.getByRole('button', { name: /^Allow once: / })).toBeVisible();
  await expect(float.getByRole('button', { name: /^Keep it out: / })).toBeVisible();
  await expect(float).not.toContainText('pipeshub-subagent');
  await expect(float).not.toContainText('mcp_e2esaas_create_ticket');
  const job = page.getByRole('group', { name: /^File the wonders ticket, / });
  await expect(job).toHaveCount(1);
  await expect(job).toHaveAttribute('data-status', 'needsYou');
  await expect(job.locator('[data-interaction-active="true"]')).toHaveCount(0);
  await expect(job.getByTestId('job-ask-pointer')).toHaveCount(1);
  await expect(page.getByText(/Code task:/)).toHaveCount(0);
  await shot(page, 's8-01-engine-pending-compact-mock');
  // Details opens classic's whole card, which names the engine and is worded like any other card.
  await float.getByRole('button', { name: 'Details' }).click();
  const card = float.getByTestId('job-ask-details').locator('[data-interaction-active="true"]');
  await expect(card).toHaveCount(1);
  await expect(card.getByTestId('child-attribution')).toHaveText(/Codex wants to:/);
  await expect(card.getByTestId('interaction-heading')).toHaveText('Create ticket in e2esaas?');
  await expect(card).not.toContainText('pipeshub-subagent');
  await expect(card).not.toContainText('mcp_e2esaas_create_ticket');
  await shot(page, 's8-01-engine-pending-mock');
});
