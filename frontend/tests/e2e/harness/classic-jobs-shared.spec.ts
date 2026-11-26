/**
 * CJ-5, a shared chat's viewer, on the harness lane: the owner's
 * turn hands its work to a loop sub-agent; a second member of the org, with the chat shared read-only, sees the job
 * card's status and now line follow the job live (the job's stream allows the share audience), and is offered no
 * Stop and none of the owner-only tabs (Brief, Activity, Machine). No button leads to a refusal: the page asks for
 * nothing the server answers with 403 or 404.
 *
 * The first test checks the viewer setup on today's chat (a member made by the admin, the share, the viewer's
 * sign-in and their read of the shared answer), so phase 1 proves it before the cards exist.
 */
import { randomBytes } from 'crypto';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { loopHeld, READ_TOOL, WRITE_TOOL } from '../fixtures/classic-jobs-scenarios';
import { adminApi } from '../helpers/labs-flag.helper';
import { lane, ScriptedModel, ScriptedSaas, type Lane } from './support/lane';
import {
  cardStatus,
  createViewer,
  DESKTOP,
  detailsButton,
  grantTools,
  handedOffJob,
  jobCard,
  jobPanel,
  jobTimeline,
  jumpRow,
  panelOwnerOnly,
  releaseUntil,
  removeViewer,
  sendJobTurn,
  shareWith,
  shoot,
  stopButton,
  viewerPage,
  type Viewer,
} from './support/classic-jobs';

// Each test sends its own turn, so one failing leaves the others to run (one worker: they share the scripted model).

let l: Lane;
let model: ScriptedModel;
let saas: ScriptedSaas;
let viewer: Viewer;
test.beforeAll(async () => {
  l = lane();
  model = await ScriptedModel.open(l);
  saas = await ScriptedSaas.open(l);
  const api = await adminApi();
  await grantTools(api, l.toolAgentKey, [READ_TOOL, WRITE_TOOL]);
  viewer = await createViewer(api);
  await api.dispose();
});
test.afterAll(async () => {
  await model.close();
  await saas.close();
  const api = await adminApi();
  await removeViewer(api, viewer);
  await api.dispose();
});
test.beforeEach(async ({ page }) => {
  test.setTimeout(300_000);
  await model.reset();
  await saas.reset();
  await page.setViewportSize(DESKTOP);
});

const tag = (): string => randomBytes(3).toString('hex');

/**
 * The page's own question "may this person open the chat's agent?" (`GET /agents/:key`): a viewer the agent is shared
 * with gets its name and menu, anyone else a quiet 404 and a read-only header (`chat-page.test.tsx`); no button sits
 * behind it. Everything else a viewer's page is refused would be a read it should not make (CJFIX-B, F3).
 */
const agentProbe = (agentKey: string) => (refused: string) => refused === `404 /api/v1/agents/${agentKey}`;

/** Every API answer the viewer's page got that refused it: a button behind one would be dead. */
function refusals(page: Page): string[] {
  const refused: string[] = [];
  page.on('response', (response) => {
    const path = new URL(response.url()).pathname;
    if (path.startsWith('/api/') && [403, 404].includes(response.status())) refused.push(`${response.status()} ${path}`);
  });
  return refused;
}

test('CJ-5 scenario (shared): a member the chat is shared with signs in and reads the finished answer', async ({ page, browser, apiContext }) => {
  const scenario = loopHeld(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { turnRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  await releaseUntil(model, async () => (await jobTimeline(apiContext, agentKey, conversationId, turnRunId)).status === 'completed', 'the turn completes');
  await shareWith(apiContext, agentKey, conversationId, viewer);

  const theirs = await viewerPage(browser, viewer);
  try {
    await theirs.goto(`/chat/?agentId=${agentKey}&conversationId=${conversationId}`);
    await expect(theirs.getByText(scenario.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  } finally {
    await theirs.context().close();
  }
});

test("CJ-5 shared viewer (D14), a finished job: the card's status, no Stop, no Details, the panel link says whose it is, no refused call", async ({ page, browser, apiContext }) => {
  const scenario = loopHeld(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { turnRunId, jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  await releaseUntil(model, async () => (await jobTimeline(apiContext, agentKey, conversationId, turnRunId)).status === 'completed', 'the turn completes');
  await shareWith(apiContext, agentKey, conversationId, viewer);

  const theirs = await viewerPage(browser, viewer);
  try {
    await theirs.setViewportSize(DESKTOP);
    const refused = refusals(theirs);
    await theirs.goto(`/chat/?agentId=${agentKey}&conversationId=${conversationId}`);
    await expect(theirs.getByText(scenario.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
    const card = jobCard(theirs, jobRunId);
    await expect(card).toHaveAttribute('data-status', 'done', { timeout: 60_000 });
    await expect(cardStatus(card)).toHaveText('Done');
    await expect(stopButton(card)).toHaveCount(0);
    // Details opens owner-only routes, so a viewer is not offered it (CJ-2).
    await expect(detailsButton(card)).toHaveCount(0);
    await shoot(theirs, 'shared-viewer-done-desktop-light');

    // A link to the job's panel still opens, and says whose details they are instead of sections it cannot read.
    await theirs.goto(`/chat/?agentId=${agentKey}&conversationId=${conversationId}&job=${jobRunId}`);
    await expect(panelOwnerOnly(theirs)).toHaveText('Only the person who started this job can see its details', { timeout: 30_000 });
    await expect(jumpRow(theirs)).toHaveCount(0);
    await expect(stopButton(jobPanel(theirs))).toHaveCount(0);
    await shoot(theirs, 'shared-viewer-panel-desktop-light');
    // No owner-only route of the job is asked for: Brief, activity, steps, terminal logs and Stop sit behind
    // controls the viewer is not offered.
    const ownerOnly = refused.filter((r) => r.includes(`/runs/${jobRunId}/`) && /\/(brief|activity|steps|commands|cancel)/.test(r));
    expect(ownerOnly, `the viewer page asked for owner-only job routes: ${refused.join(', ')}`).toEqual([]);
    // CJFIX-B (F3): nothing else either (the job's timeline, the workspace's versions); the agent probe is above.
    expect(refused.filter((r) => !agentProbe(agentKey)(r)), 'the viewer page was refused').toEqual([]);
  } finally {
    await theirs.context().close();
  }
});

test("CJ-5 shared viewer (D14), a live job: the viewer's card shows Working while the owner's job runs, then Done with no reload", async ({ page, browser, apiContext }) => {
  const scenario = loopHeld(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  await expect.poll(() => model.holding(), { message: 'the job is held working', timeout: 120_000 }).toBe(true);
  await shareWith(apiContext, agentKey, conversationId, viewer);

  const theirs = await viewerPage(browser, viewer);
  try {
    await theirs.setViewportSize(DESKTOP);
    const refused = refusals(theirs);
    await theirs.goto(`/chat/?agentId=${agentKey}&conversationId=${conversationId}`);
    const card = jobCard(theirs, jobRunId);
    await expect(card).toHaveAttribute('data-status', 'working', { timeout: 60_000 });
    await expect(cardStatus(card)).toHaveText('Working');
    await expect(stopButton(card)).toHaveCount(0);
    await shoot(theirs, 'shared-viewer-working-desktop-light');
    await releaseUntil(model, async () => (await card.getAttribute('data-status')) === 'done', "the viewer's card reads Done");
    await expect(theirs.getByText(scenario.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
    expect(refused.filter((r) => !agentProbe(agentKey)(r)), 'the viewer page was refused').toEqual([]);
  } finally {
    await theirs.context().close();
  }
});
