import { test, expect, type Page } from '@playwright/test';
import { fake, stackState } from './support/stack';
import { composer } from './support/chat-ui';
import { COMBOS, OUT_DIR, VISUAL_ENABLED, makeCast, setCurrentCombo, shot, unreachable, writeIndex, type Cast, type Person } from './support/visual-ph10';

/**
 * PH-10 screenshot review: every mention surface in desktop and mobile, light and dark, from real browser users.
 * Opt-in: `PCC_VISUAL_TOUR=1 tests/e2e/collab-chats/run.sh visual-ph10`. Files land in `test-results/visual-ph10/`.
 * It asserts only enough to know the state it photographs was reached; a state it cannot reach is listed in INDEX.md.
 */
test.skip(!VISUAL_ENABLED, 'PH-10 visual review: set PCC_VISUAL_TOUR=1');
test.describe.configure({ mode: 'serial' });

const MENTIONS = 'ENABLE_CHAT_MENTIONS';
const SEND = 'Send message';
const STREAM_URL = /\/messages\/stream$/;
/** The card `help_card.build_help_card` renders for a sender with Collections, Google Drive and Slack in a 3-person chat. */
const HELP_CARD =
  "**What I can do in this chat**\n\n- Search and answer from what *you* can access: Collections, Google Drive, Slack.\n- Answers use only your access, but everyone in this chat can read them.\n- This chat has 3 people; your message and my reply are visible to all of them.\n- Mention a teammate with @name to leave a note without asking me. Use @assistant to ask me.";

/** The composer may run the assistant on the chat route or the universal agent route: script both until one is seen used. */
let answerRoute: string | null = null;
async function answerWith(text: string): Promise<number> {
  const mark = await fake.mark();
  for (const route of answerRoute ? [answerRoute] : ['chat_stream', 'agent_chat_stream']) {
    await fake.script(route, { kind: 'stream_answer', text });
  }
  return mark;
}
async function learnRoute(mark: number): Promise<void> {
  answerRoute ??= (await fake.requests(['chat_stream', 'agent_chat_stream'], mark)).at(-1)?.route ?? null;
}

let seq = 0;
const key = (what: string) => `vph10-${what}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

async function openConversation(page: Page, id: string, agent = false): Promise<void> {
  await page.goto(`/chat/?${agent ? 'agentId=agent-1&' : ''}conversationId=${id}`);
  await expect(composer(page)).toBeVisible({ timeout: 30_000 });
}

async function typeMention(page: Page, query: string, option: string | RegExp): Promise<void> {
  await page.keyboard.type(`@${query}`);
  const list = page.getByRole('listbox', { name: 'Mention suggestions' });
  await expect(list).toBeVisible();
  await list.getByRole('option').filter({ hasText: option }).first().click();
  await expect(list).toBeHidden();
}

const popover = (page: Page) => page.getByRole('listbox', { name: 'Mention suggestions' }).locator('xpath=ancestor-or-self::*[@data-radix-popper-content-wrapper or @role="dialog"][1]');

async function stage(id: string, slug: string, run: () => Promise<void>, page?: Page): Promise<void> {
  try {
    await run();
  } catch (error) {
    const text = (error instanceof Error ? error.message : String(error)).replace(/\u001b\[[0-9;]*m/g, '');
    unreachable(id, slug, text.split('\n').slice(0, 3).join(' ').slice(0, 300));
    await page?.screenshot({ path: `${OUT_DIR}/failed-${id}-${slug}-${Date.now()}.png` }).catch(() => undefined);
  }
}

async function openInbox(p: Person): Promise<void> {
  await p.page.goto('/chat/');
  const inbox = p.page.getByText('Inbox', { exact: true });
  if ((p.page.viewportSize()?.width ?? 1440) <= 768) await p.page.getByRole('button', { name: /sidebar|menu/i }).first().click();
  await expect(inbox).toBeVisible({ timeout: 20_000 });
  await inbox.click();
}

for (const combo of COMBOS) {
  test.describe(`${combo.viewport} ${combo.theme}`, () => {
    let cast: Cast;
    let roster: ReturnType<typeof stackState>['roster'];
    test.beforeEach(async ({ browser }) => {
      setCurrentCombo(combo);
      test.setTimeout(300_000);
      roster = stackState().roster;
      await fake.reset();
      await fake.resetTips();
      cast = makeCast(browser, combo);
    });
    test.afterEach(async () => {
      await cast.close();
      await fake.reset();
    });

    test('composer placeholders and the shared empty state', async () => {
      const alice = await cast.open('owner');
      const bob = await cast.open('write_recipient');
      await stage('01', 'composer-solo-placeholder', async () => {
        const { sessionId } = await fake.seedChat({ key: key('solo') });
        await openConversation(alice.page, sessionId);
        await expect(composer(alice.page)).toHaveAttribute('contenteditable', 'true');
        await shot(alice.page, '01', 'composer-solo-placeholder', 'Solo chat with the mentions flag on: the rich composer with the solo placeholder "Ask anything · type @ for agents".', composer(alice.page).locator('xpath=ancestor::*[4]'));
      });
      await stage('02', 'composer-shared-placeholder', async () => {
        const { sessionId } = await fake.seedChat({ key: key('shared'), users: { write_recipient: 'write', read_recipient: 'read' } });
        await openConversation(bob.page, sessionId);
        await shot(bob.page, '02', 'composer-shared-placeholder', 'Bob in a shared chat: the shared placeholder "Ask PipesHub, or type @ to mention a teammate or agent" under the audience notice.', composer(bob.page).locator('xpath=ancestor::*[4]'));
      });
      await stage('03', 'shared-empty-state', async () => {
        const { sessionId } = await fake.seedChat({ key: key('empty'), users: { write_recipient: 'write' }, empty: true });
        await alice.page.goto(`/chat/?conversationId=${sessionId}`);
        const empty = alice.page.getByTestId('shared-chat-empty-state');
        await expect(empty).toBeVisible({ timeout: 30_000 });
        await shot(alice.page, '03', 'shared-empty-state', 'A shared chat with no messages yet: "Ideas for this shared chat", two example prompts, the "@ Mention a teammate" action (types @ in the composer) with its note helper text, and the @assistant help hint.', empty);
        await empty.getByTestId('shared-chat-empty-mention').click();
        await expect(alice.page.getByRole('listbox', { name: 'Mention suggestions' })).toBeVisible();
        await shot(alice.page, '03b', 'empty-state-mention-popover', 'After "@ Mention a teammate": "@" is in the composer and the mention popover is open.');
        await alice.page.keyboard.press('Escape');
      });
    });

    test('popover, chips and the ambiguous chooser', async () => {
      const alice = await cast.open('owner');
      const { teamId } = await fake.addTeam('Launch Team', { owner: 'OWNER', write_recipient: 'WRITER', team_writer: 'WRITER' });
      const { sessionId } = await fake.seedChat({ key: key('agent'), kind: 'agent', users: { write_recipient: 'write', read_recipient: 'read' }, teams: { [teamId]: 'write' } });
      await stage('04', 'popover-agent-chat', async () => {
        await openConversation(alice.page, sessionId, true);
        await composer(alice.page).click();
        await alice.page.keyboard.type('@');
        const list = alice.page.getByRole('listbox', { name: 'Mention suggestions' });
        await expect(list).toBeVisible();
        await expect(list.getByRole('option').filter({ hasText: 'Launch Team' })).toHaveCount(1, { timeout: 15_000 });
        await expect(alice.page.getByTestId('composer-tip')).toHaveAttribute('data-tip', 'intro');
        await shot(alice.page, '04', 'popover-agent-chat', 'Owner types @ in a shared agent chat: Assistant, the chat\'s agent, people (User Writer, User Reader) and the Launch Team, with the first-open intro footer (popoverIntro).', popover(alice.page));
        await alice.page.keyboard.press('Escape');
      }, alice.page);
      await stage('05', 'composer-chips', async () => {
        await composer(alice.page).fill('');
        await composer(alice.page).click();
        await typeMention(alice.page, 'Wri', 'User Writer');
        await alice.page.keyboard.type('and ');
        await typeMention(alice.page, 'Lau', 'Launch Team');
        await alice.page.keyboard.type('please check the rollout plan with ');
        await typeMention(alice.page, 'Ag', /^Agent/);
        await expect(composer(alice.page).getByTestId('mention-chip')).toHaveCount(3);
        await shot(alice.page, '05', 'composer-chips', 'Composer with three chips (a person, a team, the agent) inline with text.', composer(alice.page).locator('xpath=ancestor::*[4]'));
      }, alice.page);
      await stage('06', 'ambiguous-chooser', async () => {
        await composer(alice.page).fill('');
        await composer(alice.page).click();
        await alice.page.keyboard.insertText('hi @User can you look?');
        await alice.page.getByRole('button', { name: SEND }).click();
        const chooser = alice.page.getByTestId('mention-chooser');
        await expect(chooser).toBeVisible();
        await shot(alice.page, '06', 'ambiguous-chooser', 'Typed "@User" matches User Writer and User Reader: the chooser asks "Who is @User?" and nothing has been sent.', chooser);
      });
    });

    test('sent chips, coachmarks, help card, notes and the bell', async () => {
      const alice = await cast.open('owner');
      const bob = await cast.open('write_recipient');
      const { sessionId } = await fake.seedChat({ key: key('notes'), users: { write_recipient: 'write', read_recipient: 'read' } });
      await openConversation(bob.page, sessionId);
      await stage('07', 'coachmark-first-shared-send', async () => {
        const mark = await answerWith('The rollout starts Monday; support is briefed on Friday.');
        await composer(bob.page).click();
        await typeMention(bob.page, 'Own', 'User Owner');
        await bob.page.keyboard.type('FYI. @assistant what changed since yesterday?');
        await bob.page.getByRole('button', { name: SEND }).click();
        const tip = bob.page.getByTestId('coachmark-mentions.firstSharedSend');
        await expect(tip).toBeVisible({ timeout: 15_000 });
        await shot(bob.page, '07', 'coachmark-first-shared-send', 'Bob\'s first send in a shared chat: coachmark "Your message and the answer are visible to N people".', tip);
        await tip.getByRole('button', { name: 'Got it' }).click();
        await expect(bob.page.getByText('support is briefed on Friday')).toBeVisible({ timeout: 30_000 });
        await learnRoute(mark);
      });
      await stage('08', 'sent-message-chips', async () => {
        await expect(bob.page.getByText('support is briefed on Friday')).toBeVisible({ timeout: 30_000 });
        const heading = bob.page.getByTestId('user-query-heading').filter({ hasText: 'what changed' });
        await heading.scrollIntoViewIfNeeded();
        await expect(heading.getByTestId('mention-chip').filter({ hasText: 'User Owner' })).toHaveCount(1);
        await shot(bob.page, '08', 'sent-message-chips', 'The sent question renders the person token as a chip ("@User Owner") and the typed @assistant as an assistant chip, followed by the answer.', heading);
      });
      await stage('09', 'help-card', async () => {
        for (const route of answerRoute ? [answerRoute] : ['chat_stream', 'agent_chat_stream']) {
          await fake.script(route, { kind: 'stream_answer', text: HELP_CARD, answerMatchType: 'Capability Card' });
        }
        await composer(bob.page).click();
        await bob.page.keyboard.type('@assistant help');
        await bob.page.getByRole('button', { name: SEND }).click();
        await expect(bob.page.getByText('What I can do in this chat')).toBeVisible({ timeout: 30_000 });
        await shot(bob.page, '09', 'help-card', '"@assistant help": the capability card (text from help_card.build_help_card, served through the lane\'s fake with answerMatchType "Capability Card"), without confidence badge or Sources/Citation tabs.');
      });
      await stage('10', 'coachmark-first-note', async () => {
        await composer(bob.page).click();
        await typeMention(bob.page, 'Own', 'User Owner');
        await bob.page.keyboard.type('can you approve the launch checklist?');
        const posted = bob.page.waitForResponse((r) => /\/notes$/.test(r.url()) && r.request().method() === 'POST');
        await bob.page.getByRole('button', { name: SEND }).click();
        expect((await posted).status()).toBe(201);
        const tip = bob.page.getByTestId('coachmark-mentions.firstNote');
        await expect(tip).toBeVisible({ timeout: 15_000 });
        await shot(bob.page, '10', 'coachmark-first-note', 'Bob\'s first note: coachmark "Notes don\'t ask the AI. Mention @assistant to ask."', tip);
        await tip.getByRole('button', { name: 'Got it' }).click();
      });
      await stage('11', 'note-bubble-author', async () => {
        const note = bob.page.getByTestId('note-bubble').last();
        await expect(note).toBeVisible();
        await note.scrollIntoViewIfNeeded();
        await shot(bob.page, '11', 'note-bubble-author', 'The note in Bob\'s (author\'s) tab: NOTE label, "You" chip, time, the mention chip and the text.', note);
      });
      await stage('12', 'note-bubble-other', async () => {
        await openConversation(alice.page, sessionId);
        const note = alice.page.getByTestId('note-bubble').last();
        await expect(note).toBeVisible({ timeout: 30_000 });
        await note.scrollIntoViewIfNeeded();
        await shot(alice.page, '12', 'note-bubble-other', 'The same note in Alice\'s tab: author chip "User Writer" and a "@you" chip for the mention of her.', note);
      });
      await stage('13', 'notification-mentioned', async () => {
        await expect.poll(async () => {
          const list = await alice.api.call('GET', '/api/v1/notifications?limit=30');
          return (list.body.notifications ?? []).some((n: { type: string }) => n.type === 'chat.mentioned');
        }, { timeout: 40_000 }).toBe(true);
        await openInbox(alice);
        const row = alice.page.getByText('You were mentioned').first();
        await expect(row).toBeVisible({ timeout: 20_000 });
        await shot(alice.page, '13', 'notification-mentioned', 'Alice\'s inbox: the chat.mentioned row ("You were mentioned" / "Someone mentioned you in a chat."), no chat title or text.');
      });
    });

    test('agent mention coachmark', async () => {
      const alice = await cast.open('owner');
      const { sessionId } = await fake.seedChat({ key: key('agentsend'), kind: 'agent', users: { write_recipient: 'write' } });
      await stage('14', 'coachmark-first-agent-mention', async () => {
        await openConversation(alice.page, sessionId, true);
        await fake.script('agent_chat_stream', { kind: 'stream_answer', text: 'The agent checked the plan.' });
        await composer(alice.page).click();
        await typeMention(alice.page, 'Ag', /^Agent/);
        await alice.page.keyboard.type('check the plan');
        await alice.page.getByRole('button', { name: SEND }).click();
        const tip = alice.page.getByTestId('coachmark-mentions.firstAgentMention');
        await expect(tip).toBeVisible({ timeout: 15_000 });
        await shot(alice.page, '14', 'coachmark-first-agent-mention', 'First agent mention: coachmark "This agent answers with your access. Everyone in this chat will see its reply."', tip);
      });
    });

    test('add-to-chat prompt for the owner and for an editor', async () => {
      const alice = await cast.open('owner');
      const bob = await cast.open('write_recipient');
      const carol = await cast.open('read_recipient');
      const { sessionId } = await fake.seedChat({ key: key('former'), users: { write_recipient: 'write', read_recipient: 'write' } });
      // Carol writes a turn, then leaves the chat: she stays an author in the feed but is no longer a participant.
      await fake.script('chat_stream', { kind: 'stream_answer', text: 'Noted, Carol.' });
      const asked = await carol.api.call('POST', `/api/v1/conversations/${sessionId}/messages/stream`, { query: 'Carol here, adding the pricing risk.', chatMode: 'internal_search' });
      expect(asked.status).toBe(200);
      expect((await alice.api.revoke(sessionId, roster.read_recipient)).status).toBeLessThan(300);
      for (const [id, who, slug, text] of [
        ['15', alice, 'nonparticipant-prompt-owner', 'Owner mentions Carol, who has left: the note is posted and "Add User Reader to this chat? Can view / Can continue / Not now" appears.'],
        ['16', bob, 'nonparticipant-prompt-editor', 'An editor mentions Carol: "User Reader isn\'t in this chat. Ask the owner to add them."'],
      ] as const) {
        await stage(id, slug, async () => {
          await openConversation(who.page, sessionId);
          await expect(who.page.getByText('Carol here, adding the pricing risk.')).toBeVisible({ timeout: 30_000 });
          await composer(who.page).click();
          await typeMention(who.page, 'Rea', 'User Reader');
          await who.page.keyboard.type('the pricing risk is handled');
          const notes = who.page.getByTestId('note-bubble');
          const notesBefore = await notes.count();
          await who.page.getByRole('button', { name: SEND }).click();
          const prompt = who.page.getByTestId('non-participant-prompt');
          await expect(prompt).toBeVisible({ timeout: 15_000 });
          // The prompt can render before the posted note; the shot is meant to show both.
          await expect(notes).toHaveCount(notesBefore + 1, { timeout: 15_000 });
          await shot(who.page, id, slug, text, prompt);
        });
      }
    });

    test('error toasts', async () => {
      const alice = await cast.open('owner');
      const bob = await cast.open('write_recipient');
      await stage('17', 'toast-message-is-note', async () => {
        const { sessionId } = await fake.seedChat({ key: key('modechange'), users: { write_recipient: 'write' } });
        // Bob's tab must hold `smart` (it comes with the collaborators list) before the owner switches the mode, and must send
        // before its feed poll picks the change up; otherwise the tab knows the mode and sends a note with no request.
        const loaded = bob.page.waitForResponse((r) => r.url().includes(`/conversations/${sessionId}/collaborators`) && r.request().method() === 'GET' && r.ok());
        await openConversation(bob.page, sessionId);
        await loaded;
        await composer(bob.page).click();
        await bob.page.keyboard.type('is the launch still on track?');
        expect((await alice.api.call('PATCH', `/api/v1/conversations/${sessionId}/collaboration-settings`, { respondMode: 'mention_only' })).status).toBe(200);
        const refused = bob.page.waitForResponse((r) => STREAM_URL.test(r.url()) && r.request().method() === 'POST');
        await bob.page.getByRole('button', { name: SEND }).click();
        expect((await refused).status()).toBe(422);
        const toast = bob.page.getByText(/wasn't sent to the AI/);
        await expect(toast).toBeVisible({ timeout: 10_000 });
        await shot(bob.page, '17', 'toast-message-is-note', 'MESSAGE_IS_NOTE toast (stale respond mode in Bob\'s tab); the text is back in the composer.');
      });
      await stage('18', 'toast-mention-not-allowed', async () => {
        const { sessionId } = await fake.seedChat({ key: key('ghost'), users: { write_recipient: 'write' } });
        // The UI only offers the chat's own teams; the list is padded with a team that is not on the chat so the server refuses it.
        await alice.page.route('**/collaborators', async (route) => {
          if (route.request().method() !== 'GET') return route.continue();
          const response = await route.fetch();
          const json = await response.json();
          json.collaborators = [...(json.collaborators ?? []), { principalType: 'team', principalId: 'team-not-on-this-chat', displayName: 'Ghost Team', accessLevel: 'read', state: 'active' }];
          await route.fulfill({ response, json });
        });
        await openConversation(alice.page, sessionId);
        await composer(alice.page).click();
        await typeMention(alice.page, 'Gho', 'Ghost Team');
        await alice.page.keyboard.type('heads up');
        await alice.page.getByRole('button', { name: SEND }).click();
        const toast = alice.page.getByText(/can't be mentioned in this chat/);
        await expect(toast).toBeVisible({ timeout: 10_000 });
        await shot(alice.page, '18', 'toast-mention-not-allowed', 'MENTION_NOT_ALLOWED toast from the server (a team that is not on the chat was injected into the client list).');
      });
    });

    test('preferences and the flag-off composer', async () => {
      const bob = await cast.open('write_recipient');
      await stage('19', 'profile-mention-switches', async () => {
        await bob.page.goto('/workspace/profile/');
        const section = bob.page.getByText('Chat notifications', { exact: true });
        await expect(section).toBeVisible({ timeout: 30_000 });
        const mentioned = bob.page.getByText(/mention/i).last();
        await mentioned.scrollIntoViewIfNeeded();
        await shot(bob.page, '19', 'profile-mention-switches', 'Profile, "Chat notifications": the in-app and email switches for mentions next to the PH-06 ones.');
      });
      await stage('20', 'composer-flag-off', async () => {
        const alice = await cast.open('owner');
        const { sessionId } = await fake.seedChat({ key: key('flagoff') });
        await fake.setFlag(false, MENTIONS);
        try {
          await openConversation(alice.page, sessionId);
          await expect(composer(alice.page)).toHaveJSProperty('tagName', 'TEXTAREA');
          await shot(alice.page, '20', 'composer-flag-off', 'Mentions flag off: the plain textarea composer and its old placeholder, for comparison with 01.', composer(alice.page).locator('xpath=ancestor::*[4]'));
        } finally {
          await fake.setFlag(true, MENTIONS);
        }
      });
    });
  });
}

test('zz write INDEX.md', () => {
  writeIndex();
});
