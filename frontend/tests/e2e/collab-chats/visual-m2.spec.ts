import { test, expect, type Page } from '@playwright/test';
import { fake, stackState } from './support/stack';
import { composer, openShareDrawer, shareWith } from './support/chat-ui';
import { COMBOS, OUT_DIR, VISUAL_ENABLED, makeCast, setCurrentCombo, shot, unreachable, writeIndex, type Cast } from './support/visual-m2';

/**
 * M2 screenshot review: a guest agent in a plain chat (picker, typed handle, attributed answer), people search across the
 * organization (email, "Not in this chat"), the Add people row and its drawer, sharing a new chat before it exists, and the
 * picker following a share without a reload. Desktop and mobile, light and dark.
 * Opt-in: `PCC_VISUAL_TOUR=1 tests/e2e/collab-chats/run.sh visual-m2`. Files land in `test-results/visual-m2/`.
 */
test.skip(!VISUAL_ENABLED, 'M2 visual review: set PCC_VISUAL_TOUR=1');
test.describe.configure({ mode: 'serial' });

const AGENT = { _key: 'agent-joke', name: 'Joke Buddy', handle: 'joke-buddy', createdBy: '', isServiceAccount: false };
let seq = 0;
const key = (what: string) => `vm2-${what}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

const list = (page: Page) => page.getByRole('listbox', { name: 'Mention suggestions' });
const popover = (page: Page) => list(page).locator('xpath=ancestor-or-self::*[@data-radix-popper-content-wrapper or @role="dialog"][1]');

async function openConversation(page: Page, id: string): Promise<void> {
  await page.goto(`/chat/?conversationId=${id}`);
  await expect(composer(page)).toBeVisible({ timeout: 30_000 });
}

async function scriptAgents(createdBy: string): Promise<void> {
  const listing = { kind: 'reply' as const, body: { success: true, agents: [{ ...AGENT, createdBy }], pagination: { currentPage: 1, limit: 100, totalItems: 1, totalPages: 1 } } };
  await fake.script('agent_list', ...Array.from({ length: 60 }, () => listing));
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
      await fake.resetTips();
      cast = makeCast(browser, combo);
    });
    test.afterEach(async () => {
      await cast.close();
      await fake.reset();
    });

    test('a guest agent in a plain chat', async ({ browser }) => {
      // The agent list is cached per user for a minute, so this user is new: earlier specs may have cached an empty one.
      const own = makeCast(browser, combo, { owner: await fake.freshActor(`m2agent${Date.now().toString(36)}`) });
      const alice = await own.open('owner');
      const { page } = alice;
      await scriptAgents(alice.actor.userId);
      const sessionId = await alice.api.startChat(key('agent'));
      await openConversation(page, sessionId);
      await stage('01', 'picker-agent-default-chat', async () => {
        await composer(page).click();
        await page.keyboard.type('@joke');
        await expect(list(page).getByRole('option').filter({ hasText: '@joke-buddy' })).toHaveCount(1, { timeout: 20_000 });
        await shot(page, '01', 'picker-agent-default-chat', 'A plain chat (no agent of its own): typing @joke offers the agent under an "Agents" heading with its avatar and @joke-buddy.', popover(page));
        await page.keyboard.press('Escape');
      }, page);
      await stage('02', 'typed-handle-answer-attributed', async () => {
        await composer(page).click();
        await page.keyboard.press('Control+A');
        await page.keyboard.press('Delete');
        await page.keyboard.type('tell me a joke @joke-buddy ');
        await fake.script('agent_chat_stream', { kind: 'stream_answer', text: 'Why do programmers prefer dark mode? Because light attracts bugs.' });
        await page.getByRole('button', { name: 'Send message' }).click();
        await expect(page.getByText('light attracts bugs')).toBeVisible({ timeout: 45_000 });
        const header = page.getByTestId('agent-answer-header').last();
        await expect(header).toContainText('Joke Buddy');
        await shot(page, '02', 'typed-handle-answer-attributed', 'The handle typed by hand became the agent chip; the answer is headed by the agent\'s avatar and name "Joke Buddy" (its @handle is in the tooltip).', header.locator('xpath=ancestor::*[3]'));
      }, page);
      await own.close();
    });

    test('people from the whole organization, the Add people row and its drawer', async () => {
      const alice = await cast.open('owner');
      const { page } = alice;
      const { sessionId } = await fake.seedChat({ key: key('people') });
      await openConversation(page, sessionId);
      await stage('03', 'people-outside-the-chat', async () => {
        await composer(page).click();
        await page.keyboard.type('@user wri');
        const option = list(page).getByRole('option').filter({ hasText: 'Not in this chat' }).first();
        await expect(option).toBeVisible({ timeout: 20_000 });
        await expect(option).toContainText('@');
        await shot(page, '03', 'people-outside-the-chat', 'A multi-word query ("user wri") in a chat nobody else is in: the colleague is listed with their email under the name and a "Not in this chat" badge; the popover stayed open across the space; the "Add people to this chat…" row closes the list.', popover(page));
      }, page);
      await stage('04', 'add-people-drawer-prefilled', async () => {
        await list(page).getByRole('option', { name: 'Add people to this chat…' }).click();
        const dialog = page.getByRole('dialog', { name: 'Share chat' });
        await expect(dialog).toBeVisible({ timeout: 15_000 });
        await expect(dialog.getByPlaceholder(/Emails, teams or names/)).toHaveValue('user wri');
        await shot(page, '04', 'add-people-drawer-prefilled', 'The share drawer opened by the Add people row, with the typed query in its people search; the composer behind has lost the @query and kept the draft.');
        await page.keyboard.press('Escape');
        await expect(dialog).toBeHidden();
        await expect(composer(page)).toBeFocused();
      }, page);
    });

    test('sharing a new chat before it exists', async () => {
      const alice = await cast.open('owner');
      const bob = stackState().roster.write_recipient;
      const { page } = alice;
      await page.goto('/chat/');
      await expect(composer(page)).toBeVisible({ timeout: 30_000 });
      await stage('05', 'new-chat-share-in-header', async () => {
        const share = page.getByRole('button', { name: 'Share', exact: true }).first();
        await expect(share).toBeEnabled({ timeout: 20_000 });
        await shot(page, '05', 'new-chat-share-in-header', 'The new-chat page: the Share entry is in the header although the chat does not exist yet.');
        await share.click();
        const dialog = page.getByRole('dialog', { name: 'Share chat' });
        await expect(dialog).toBeVisible();
        await expect(dialog.getByRole('note')).toContainText('Nothing is shared yet');
        await shot(page, '05b', 'new-chat-draft-drawer', 'The share drawer in draft mode: its notice says nothing is shared until the first message, and the button reads "Add".');
        await dialog.getByPlaceholder(/Emails, teams or names/).fill(bob.email);
        await dialog.getByRole('checkbox', { name: 'User Writer' }).click();
        await dialog.getByRole('button', { name: 'Add', exact: true }).click();
        await page.keyboard.press('Escape');
        await expect(dialog).toBeHidden();
      }, page);
      await stage('06', 'will-be-shared-with-line', async () => {
        const line = page.getByTestId('draft-share-line');
        await expect(line).toContainText('Will be shared with');
        await expect(line).toContainText('User Writer');
        await shot(page, '06', 'will-be-shared-with-line', 'Above the composer: "Will be shared with" and the picked person with their level, removable; nothing has been sent to the server.', line);
      }, page);
      await stage('07', 'new-chat-picker-with-add-people-row', async () => {
        await composer(page).click();
        await page.keyboard.type('@');
        await expect(list(page).getByRole('option', { name: 'Add people to this chat…' })).toBeVisible({ timeout: 20_000 });
        await expect(list(page).getByRole('option').filter({ hasText: 'User Writer' }).first()).toBeVisible();
        await shot(page, '07', 'new-chat-picker-with-add-people-row', 'The picker on a new chat: the drafted colleague is listed as in the chat, and the Add people row closes the list.', popover(page));
        await page.keyboard.press('Escape');
      }, page);
      await stage('08', 'after-first-send', async () => {
        await fake.script('chat_stream', { kind: 'stream_answer', text: 'Here is the plan for the launch.' });
        await fake.script('agent_chat_stream', { kind: 'stream_answer', text: 'Here is the plan for the launch.' });
        await composer(page).fill('Plan the launch');
        await page.getByRole('button', { name: 'Send message' }).click();
        await expect(page.getByText('Here is the plan for the launch.')).toBeVisible({ timeout: 45_000 });
        await expect(page.getByTestId('draft-share-line')).toHaveCount(0);
        await shot(page, '08', 'after-first-send', 'After the first send: the chat exists, is shared with the picked person (header avatars) and the "will be shared" line is gone.');
      }, page);
    });

    test('the picker follows a share without a reload', async () => {
      const alice = await cast.open('owner');
      const bob = stackState().roster.write_recipient;
      const { page } = alice;
      const { sessionId } = await fake.seedChat({ key: key('follow') });
      await openConversation(page, sessionId);
      await stage('09', 'share-then-mention', async () => {
        await openShareDrawer(page);
        await shareWith(page, bob, { level: 'Can continue' });
        await page.keyboard.press('Escape');
        await composer(page).click();
        await page.keyboard.type('@writer');
        const option = list(page).getByRole('option').filter({ hasText: 'User Writer' }).first();
        await expect(option).toBeVisible({ timeout: 15_000 });
        await expect(option).not.toContainText('Not in this chat');
        await shot(page, '09', 'share-then-mention', 'Right after sharing with the colleague (no reload): @writer lists them as someone in the chat, with no "Not in this chat" badge.', popover(page));
      }, page);
    });
  });
}

test.afterAll(() => {
  writeIndex();
});
