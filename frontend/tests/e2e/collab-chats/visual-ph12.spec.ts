import { test, expect, type Page } from '@playwright/test';
import { NodeApi, fake } from './support/stack';
import { composer } from './support/chat-ui';
import { COLLAB_FLAGS, chatTitle, setFlags, shareChat } from './support/collab.helper';
import { COMBOS, VISUAL_ENABLED, makeCast, setCurrentCombo, shot, unreachable, writeIndex, OUT_DIR, type Cast } from './support/visual-ph12';

/**
 * PH-12 screenshot review of the UI PH-12 changed that the PH-10 tour does not show: the owner-inactive banner (with the
 * viewer banner beside it), an editor's composer, the inbox filter label, and the chat page with every collaboration
 * flag off. The coachmark and note-time contrast fixes are in `visual-ph10` ("sent chips, coachmarks, ...").
 * Opt-in: `PCC_VISUAL_TOUR=1 tests/e2e/collab-chats/run.sh visual-ph12`. Files land in `test-results/visual-ph12/`.
 */
test.skip(!VISUAL_ENABLED, 'PH-12 visual review: set PCC_VISUAL_TOUR=1');
test.describe.configure({ mode: 'serial' });

async function stage(id: string, slug: string, run: () => Promise<void>, page?: Page): Promise<void> {
  try {
    await run();
  } catch (error) {
    const text = (error instanceof Error ? error.message : String(error)).replace(/\u001b\[[0-9;]*m/g, '');
    unreachable(id, slug, text.split('\n').slice(0, 3).join(' ').slice(0, 300));
    await page?.screenshot({ path: `${OUT_DIR}/failed-${id}-${slug}-${Date.now()}.png` }).catch(() => undefined);
  }
}

async function openChat(page: Page, id: string): Promise<void> {
  await page.goto(`/chat/?conversationId=${id}`);
  await expect(page.locator('[data-testid="user-query-heading"], [data-testid="human-message"]').first()).toBeVisible({ timeout: 30_000 });
}

for (const combo of COMBOS) {
  test.describe(`${combo.viewport} ${combo.theme}`, () => {
    let cast: Cast;
    const apis: NodeApi[] = [];
    test.beforeEach(async ({ browser }) => {
      setCurrentCombo(combo);
      test.setTimeout(300_000);
      await fake.reset();
      cast = makeCast(browser, combo);
    });
    test.afterEach(async () => {
      await Promise.all(apis.splice(0).map((api) => api.deleteCreated()));
      await cast.close();
      await fake.reset();
    });

    test('banners and the composer of a shared chat', async () => {
      const writer = await cast.open('write_recipient');
      const reader = await cast.open('read_recipient');
      const owner = new NodeApi(await fake.freshActor(`vph12-${combo.viewport}-${combo.theme}`));
      apis.push(owner);
      const chat = await owner.startChat(chatTitle('vph12', 'Summarise the Q3 hiring plan'));
      await shareChat(owner, chat, { userId: writer.actor.userId, level: 'write' });
      await shareChat(owner, chat, { userId: reader.actor.userId, level: 'read' });

      await stage('01', 'editor-composer', async () => {
        await openChat(writer.page, chat);
        await expect(composer(writer.page)).toBeVisible();
        await shot(writer.page, '01', 'editor-composer', 'An editor (Can continue) in a shared chat whose owner is active: the composer, no banner.');
      }, writer.page);
      await stage('02', 'viewer-read-only-banner', async () => {
        await openChat(reader.page, chat);
        await expect(reader.page.getByTestId('read-only-banner')).toBeVisible();
        await shot(reader.page, '02', 'viewer-read-only-banner', 'A viewer: the read-only banner (eye icon) in place of the composer, for comparison with 03.', reader.page.getByTestId('read-only-banner'));
      }, reader.page);
      await stage('03', 'owner-inactive-banner', async () => {
        // Node remembers an owner's status for 60 s (D11), so this is another owner, disabled before the editor opens.
        const goneActor = await fake.freshActor(`vph12-gone-${combo.viewport}-${combo.theme}`);
        const gone = new NodeApi(goneActor);
        apis.push(gone);
        const inactive = await gone.startChat(chatTitle('vph12', 'Summarise the Q3 hiring plan'));
        await shareChat(gone, inactive, { userId: writer.actor.userId, level: 'write' });
        await fake.setDisabled(goneActor.userId, true);
        await openChat(writer.page, inactive);
        const banner = writer.page.getByTestId('read-only-banner');
        await expect(banner).toContainText('no longer active', { timeout: 20_000 });
        await expect(composer(writer.page)).toHaveCount(0);
        await shot(writer.page, '03', 'owner-inactive-banner', 'The same editor after the owner was disabled: lock icon and "The owner of this chat is no longer active, so it can\'t be continued." instead of the composer; history still shown.', banner);
      }, writer.page);
    });

    test('the inbox filter label', async () => {
      const writer = await cast.open('write_recipient');
      const owner = new NodeApi(await fake.freshActor(`vph12-inbox-${combo.viewport}-${combo.theme}`));
      apis.push(owner);
      const chat = await owner.startChat(chatTitle('vph12-inbox', 'Plan the offsite'));
      await shareChat(owner, chat, { userId: writer.actor.userId, level: 'write' }, 'Can you take this over?');
      const { page } = writer;
      await stage('04', 'inbox-filter-label', async () => {
        await page.goto('/chat/');
        if ((page.viewportSize()?.width ?? 1440) <= 768) await page.getByRole('button', { name: /sidebar|menu/i }).first().click();
        const inbox = page.getByText('Inbox', { exact: true });
        await expect(inbox).toBeVisible({ timeout: 20_000 });
        await inbox.click();
        const panel = page.getByText('Chat shared with you').first().locator('xpath=ancestor::*[.//*[text()="Inbox"]][1]');
        await expect(page.getByText('Chat shared with you').first()).toBeVisible({ timeout: 20_000 });
        await shot(page, '04', 'inbox-filter-label', 'The inbox with a share notification: the filter label under the title ("All") in full-contrast secondary text, not faded.', panel);
      }, page);
    });

    test('every collaboration flag off', async () => {
      const owner = await cast.open('owner');
      const chat = await owner.api.startChat(chatTitle('vph12-flagoff', 'Plain question, flags off'));
      apis.push(owner.api);
      await setFlags(Object.fromEntries(COLLAB_FLAGS.map((k) => [k, false])));
      try {
        await stage('05', 'flag-off-chat', async () => {
          await openChat(owner.page, chat);
          await expect(owner.page.locator('textarea[data-testid="chat-composer"]')).toBeVisible();
          await shot(owner.page, '05', 'flag-off-chat', 'All three flags off: an owner\'s chat as before the feature (plain textarea composer, no access button, no author chips).');
        }, owner.page);
        await stage('06', 'flag-off-new-chat', async () => {
          await owner.page.goto('/chat/');
          await expect(owner.page.locator('textarea[data-testid="chat-composer"]')).toBeVisible({ timeout: 30_000 });
          await shot(owner.page, '06', 'flag-off-new-chat', 'All three flags off: the new-chat page with the plain textarea composer.');
        }, owner.page);
      } finally {
        await setFlags(Object.fromEntries(COLLAB_FLAGS.map((k) => [k, true])));
      }
    });
  });
}

test('zz write INDEX.md', () => {
  writeIndex();
});
