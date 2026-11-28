/**
 * QA-CLASSIC: what the QA journeys, UX captures and latency specs share about the classic chat, the job card and
 * panel, the Jobs page and Needs you. Locators go by role or accessible name; a test id only where the page names
 * a region no role describes (a job card, a Needs you row).
 */
import { expect, type Locator, type Page } from '@playwright/test';
import { chatInput, trackTurn } from '../harness/support/lane';

export const composer = chatInput;

/** The agent's new chat, ready for a message. */
export async function openAgentChat(page: Page, agentKey: string): Promise<void> {
  await page.goto(`/chat/?agentId=${agentKey}`);
  await expect(composer(page)).toBeEditable({ timeout: 30_000 });
  await page.evaluate(() => document.fonts.ready);
}

/** The default chat's new chat (no agent), ready for a message. */
export async function openDefaultChat(page: Page): Promise<void> {
  await page.goto('/chat/');
  await expect(composer(page)).toBeEditable({ timeout: 30_000 });
  await page.evaluate(() => document.fonts.ready);
}

/** Types a turn into the agent's new chat; returns the conversation the page moved to (tracked for cleanup). */
export async function sendInNewChat(page: Page, text: string, agentKey: string): Promise<string> {
  await openAgentChat(page, agentKey);
  await composer(page).fill(text);
  await composer(page).press('Enter');
  await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
  const id = new URL(page.url()).searchParams.get('conversationId') as string;
  trackTurn(agentKey, id);
  return id;
}

/**
 * Text of the answer, never the person's message: the message is the whole script on one element, so an exact
 * match on one line of it finds only the answer's paragraph.
 */
export const answerText = (page: Page, text: string): Locator => page.getByText(text, { exact: true });

/** A settled answer draws its actions (copy, retry); a streaming one does not. */
export const answerActions = (page: Page): Locator => page.getByTestId('message-actions');

/** The sidebar's Needs you entry, its count and the Jobs entry (classic jobs D6). */
export const needsYouEntry = (page: Page): Locator => page.getByRole('link', { name: /^Needs you/ });
export const jobsEntry = (page: Page): Locator => page.getByRole('link', { name: /^Jobs/ });
export const needsYouBadge = (page: Page): Locator => page.getByRole('img', { name: /things? needs? you$/ });
export const badgeName = (count: number): string => (count === 1 ? '1 thing needs you' : `${count} things need you`);

/** A sidebar chat row's mark (working, needs you, finished since you looked). */
export const chatMark = (page: Page, conversationId: string): Locator =>
  page.locator(`a[href*="conversationId=${conversationId}"]`).first().getByTestId('chat-dot');

/**
 * An agent's sidebar keeps Recents folded until asked. The fold says nothing of its state to assistive tech
 * (follow-up QA-CLASSIC-FU-1), so it is toggled until the row shows: each try flips it once and waits for the row.
 */
export async function showRecents(page: Page, conversationId: string): Promise<void> {
  const row = page.locator(`a[href*="conversationId=${conversationId}"]`).first();
  await expect(async () => {
    const fold = page.getByRole('button', { name: 'Recents', exact: true });
    if (!(await row.isVisible()) && (await fold.getAttribute('aria-expanded')) !== 'true') await fold.click();
    await expect(row).toBeVisible({ timeout: 3_000 });
  }).toPass({ timeout: 30_000 });
}

export const JOBS = { needsYou: '/jobs/?view=needs-you', all: '/jobs/?view=all' } as const;
export const needsYouRows = (page: Page): Locator => page.getByTestId('needs-you-row');
export const needsYouRowOf = (page: Page, conversationId: string): Locator =>
  needsYouRows(page).filter({ has: page.locator(`a[href*="conversationId=${conversationId}"]`) });
export const jobsSection = (page: Page, name: 'Needs you' | 'Working' | 'Up next' | 'Finished'): Locator =>
  page.getByRole('region', { name, exact: true });
export const jobRow = (page: Page, runId: string): Locator => page.locator(`[data-testid="job-row"][data-run-id="${runId}"]`);

/** A job card in the chat: the newest, or the one named by its goal. */
export const jobCard = (page: Page, title?: string): Locator =>
  title ? page.getByTestId('job-card').filter({ hasText: title }) : page.getByTestId('job-card').last();
export const jobPanel = (page: Page): Locator => page.getByTestId('activity-panel');

/** An answer's steps, open (a finished turn folds them; a reattach can fold them again). */
export async function openSteps(page: Page): Promise<void> {
  const fold = page.getByTestId('activity-summary').last();
  await expect(fold).toBeVisible({ timeout: 60_000 });
  if ((await fold.getAttribute('aria-expanded')) === 'false') await fold.click();
  await expect(fold).toHaveAttribute('aria-expanded', 'true');
}

/** The loop discloses MCP tools lazily: a scripted turn finds a tool first, as a model would. */
export const discover = (tool: string): string => `tool search_tools ${JSON.stringify({ query: tool, make_visible: true })}`;
