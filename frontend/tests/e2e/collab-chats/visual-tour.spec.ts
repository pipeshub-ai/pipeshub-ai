import { test, expect, type Page } from '@playwright/test';
import { fake, waitFor } from './support/stack';
import { authorOf, composer, openChat } from './support/chat-ui';
import { COMBOS, TOUR_ENABLED, makeCast, markExhausted, reserveMutations, waitQuiet, observe, setCurrentCombo, shot, unreachable, writeIndex, type Cast } from './support/visual-tour';

/**
 * Visual QA tour: screenshots of every collaborative-chats screen, in desktop and mobile, light and dark.
 * Opt-in: `PCC_VISUAL_TOUR=1 tests/e2e/collab-chats/run.sh visual-tour`. Files land in `test-results/visual-tour/`.
 * It asserts only enough to be sure the state it photographs was reached.
 */
test.skip(!TOUR_ENABLED, 'visual tour: set PCC_VISUAL_TOUR=1');

const ANSWER = 'The Q3 launch is scheduled for **October 14**. Marketing sign-off is done; the remaining open item is the pricing page.';
const ANSWER_B = 'Pricing is final: the Team plan stays at **$12 per seat per month**, billed annually.';
const Q1 = 'When is the Q3 launch?';
const Q2 = 'What is the final Team plan price?';

const scriptAnswer = (text: string, runId = 'run-fixed') => fake.script('chat_stream', { kind: 'stream_answer', text, runId });

async function ownerChat(cast: Cast, opts: { shareBob?: 'read' | 'write'; shareCarol?: 'read' | 'write'; extraMutations?: number } = {}) {
  await reserveMutations((opts.shareBob ? 1 : 0) + (opts.shareCarol ? 1 : 0) + (opts.extraMutations ?? 0));
  const alice = await cast.open('owner');
  await scriptAnswer(ANSWER);
  const chat = await alice.api.startChat(Q1);
  const bob = await cast.open('write_recipient');
  const carol = await cast.open('read_recipient');
  if (opts.shareBob) expect((await alice.api.share(chat, bob.actor, opts.shareBob, 'Please take it from here.')).status).toBe(200);
  if (opts.shareCarol) expect((await alice.api.share(chat, carol.actor, opts.shareCarol)).status).toBe(200);
  return { chat, alice, bob, carol };
}

async function ensureSidebar(page: Page) {
  const inbox = page.getByText('Inbox', { exact: true });
  if ((page.viewportSize()?.width ?? 1440) > 768) {
    await expect(inbox).toBeVisible({ timeout: 20_000 });
    return;
  }
  await page.getByRole('button', { name: /sidebar|menu/i }).first().click();
  await expect(inbox).toBeVisible();
}

const drawerOf = (page: Page) => page.getByRole('dialog', { name: 'Share chat' });
// The innermost block holding both the composer and the text above it (the composer is a rich editor, not a textarea).
const composerBlock = (page: Page, above: string) =>
  page.locator('div').filter({ has: composer(page) }).filter({ has: page.getByText(above) }).last();

for (const combo of COMBOS) {
  test.describe(`${combo.viewport} ${combo.theme}`, () => {
    let cast: Cast;
    test.beforeEach(async ({ browser }) => {
      setCurrentCombo(combo);
      test.setTimeout(240_000);
      await fake.reset();
      await fake.setFlag(true);
      cast = makeCast(browser, combo);
    });
    test.afterEach(async () => {
      await fake.setFlag(true);
      await fake.reset();
      await cast.close();
    });

    test('01-02 solo chat, flag on and off', async () => {
      const { chat, alice } = await ownerChat(cast);
      await openChat(alice.page, chat);
      await expect(composer(alice.page)).toBeVisible();
      await shot(alice.page, '01', 'solo-chat-flag-on', 'A solo chat as the owner, collaboration flag ON: composer and header; the only collaboration chrome should be the Access button.');
      await fake.setFlag(false);
      await openChat(alice.page, chat);
      await expect(composer(alice.page)).toBeVisible();
      await shot(alice.page, '02', 'solo-chat-flag-off', 'The same solo chat with the collaboration flag OFF, for comparison with 01.');
    });

    test('03 share drawer as owner', async () => {
      const { chat, alice, bob, carol } = await ownerChat(cast, { extraMutations: 1 });
      await openChat(alice.page, chat);
      await alice.page.getByRole('button', { name: 'Share', exact: true }).first().click();
      const dialog = drawerOf(alice.page);
      await expect(dialog).toBeVisible();
      await shot(alice.page, '03', 'share-drawer-empty', 'Share drawer opened by the owner, nothing selected yet.', dialog);

      const search = dialog.getByRole('textbox').first();
      await search.fill(bob.actor.email);
      await dialog.getByRole('checkbox', { name: 'User Writer' }).click();
      await search.fill(carol.actor.email);
      await dialog.getByRole('checkbox', { name: 'User Reader' }).click();
      await dialog.getByRole('button', { name: /Can view/ }).first().click();
      await alice.page.getByRole('menuitemradio', { name: /Can continue/ }).click();
      await dialog.getByLabel('Message to recipients (optional)').fill('Bob, you take the pricing questions; Carol, FYI only.');
      await shot(alice.page, '04', 'share-drawer-selected', 'Share drawer with Bob and Carol selected (one level for the batch: Can continue) and a handover note typed.', dialog);

      await dialog.getByRole('button', { name: /Can continue/ }).first().click();
      await expect(alice.page.getByRole('menuitemradio').first()).toBeVisible();
      await shot(alice.page, '05', 'share-drawer-role-menu', 'Role menu open in the share drawer (Can continue / Can view).', dialog);
      await alice.page.getByRole('menuitemradio', { name: /Can continue/ }).click();

      const shared = alice.page.waitForResponse((r) => r.url().includes('/collaborators') && r.request().method() === 'PUT');
      await dialog.getByRole('button', { name: 'Share', exact: true }).click();
      expect((await shared).status()).toBe(200);
      await expect(dialog.getByText('User Reader')).toBeVisible();
      await shot(alice.page, '06', 'share-drawer-members-settings', 'Share drawer after sharing: member rows and the settings toggles (Editors can invite, Share my files and tool results).', dialog);
    });

    test('03b share drawer member rows', async () => {
      const { chat, alice } = await ownerChat(cast, { shareBob: 'write', shareCarol: 'write', extraMutations: 2 });
      await openChat(alice.page, chat);
      await alice.page.getByRole('button', { name: 'Share', exact: true }).first().click();
      const dialog = drawerOf(alice.page);
      await expect(dialog.getByText('User Reader')).toBeVisible();
      await dialog.getByRole('button', { name: /Can continue/ }).nth(1).click();
      await expect(alice.page.getByRole('menuitemradio').first()).toBeVisible();
      await shot(alice.page, '07', 'share-drawer-row-role-menu', 'Share drawer: the role menu of an existing member row (Can continue selected), with the Remove entry if any.', dialog);
      await alice.page.getByRole('menuitemradio', { name: /Can view/ }).click();
      await dialog.getByRole('switch').first().click();
      await shot(alice.page, '08', 'share-drawer-settings-on', 'Share drawer after setting Carol to Can view and switching Editors can invite on.', dialog);
    });

    test('04 org-wide and large-team confirm', async () => {
      await fake.addTeam('Everyone at Acme', { owner: 'OWNER' }, 'all_acme');
      await fake.addTeam('Field Sales', { owner: 'OWNER' });
      const { chat, alice } = await ownerChat(cast);
      await alice.page.route('**/api/v1/teams/user/teams*', async (route) => {
        const response = await route.fetch();
        const json = await response.json();
        for (const t of json.teams ?? []) if (t.name === 'Field Sales') t.memberCount = 120;
        await route.fulfill({ response, json });
      });
      await openChat(alice.page, chat);
      await alice.page.getByRole('button', { name: 'Share', exact: true }).first().click();
      const dialog = drawerOf(alice.page);
      const search = dialog.getByRole('textbox').first();

      await search.fill('Field');
      await dialog.getByRole('checkbox', { name: /Field Sales/ }).click();
      await dialog.getByRole('button', { name: 'Share', exact: true }).click();
      const confirm = alice.page.getByRole('alertdialog').or(alice.page.getByRole('dialog', { name: /large team/i }));
      await expect(confirm.first()).toBeVisible();
      await shot(alice.page, '10', 'confirm-large-team', 'Confirm dialog before sharing with a team of more than 50 members ("Field Sales" shown with 120 members; the count is injected into the team list because the fake team service returns none).', confirm);
      await alice.page.keyboard.press('Escape');

      await dialog.getByRole('button', { name: 'Cancel' }).first().click().catch(() => undefined);
      await alice.page.getByRole('button', { name: 'Share', exact: true }).first().click().catch(() => undefined);
      const search2 = drawerOf(alice.page).getByRole('textbox').first();
      await search2.fill('Everyone');
      await drawerOf(alice.page).getByRole('checkbox', { name: /Everyone at Acme/ }).click();
      await drawerOf(alice.page).getByRole('button', { name: 'Share', exact: true }).click();
      await expect(alice.page.getByText('Share with the whole organization?')).toBeVisible();
      await shot(alice.page, '09', 'confirm-org-wide', 'Confirm dialog before sharing with the whole organization (the all_ team).', alice.page.getByRole('alertdialog').or(alice.page.getByRole('dialog', { name: /whole organization/i })));
    });

    test('05 share drawer summary view (stale editor tab)', async () => {
      const { chat, alice, bob } = await ownerChat(cast, { shareBob: 'write', extraMutations: 2 });
      expect((await alice.api.call('PATCH', `/api/v1/conversations/${chat}/collaboration-settings`, { editorsCanInvite: true })).status).toBe(200);
      let freeze = false;
      await bob.page.route('**/feed**', (route) => (freeze ? route.abort() : route.continue()));
      await openChat(bob.page, chat);
      await expect(bob.page.getByRole('button', { name: 'Share', exact: true }).first()).toBeVisible();
      await expect(authorOf(bob.page, Q1)).toHaveText('User Owner', { timeout: 30_000 });
      freeze = true;
      expect((await alice.api.call('PATCH', `/api/v1/conversations/${chat}/collaboration-settings`, { editorsCanInvite: false })).status).toBe(200);
      await bob.page.getByRole('button', { name: 'Share', exact: true }).first().click();
      const dialog = drawerOf(bob.page);
      await expect(dialog.getByText(/Owner: /)).toBeVisible();
      await shot(bob.page, '11', 'share-drawer-summary', 'Share drawer in summary mode (owner, count of people, your access). Only reachable from a tab whose permissions are stale: readers and non-inviting editors never see the Share button.', dialog);
      observe('11', 'A reader (Carol) has no Share button at all, so the summary view cannot be opened as Carol; it is only reachable from a stale editor tab after the owner turned "Editors can invite" off.');
    });

    test('06 invite mode as editor', async () => {
      const { chat, alice, bob } = await ownerChat(cast, { shareBob: 'write', shareCarol: 'read', extraMutations: 1 });
      expect((await alice.api.call('PATCH', `/api/v1/conversations/${chat}/collaboration-settings`, { editorsCanInvite: true })).status).toBe(200);
      await openChat(bob.page, chat);
      await bob.page.getByRole('button', { name: 'Share', exact: true }).first().click();
      const dialog = drawerOf(bob.page);
      await expect(dialog.getByText('User Owner')).toBeVisible();
      await shot(bob.page, '12', 'share-drawer-invite-mode', 'Share drawer as Bob (editor) with Editors can invite on: can add people but not change or remove rows.', dialog);
    });

    test('07 author chips and answered-as labels', async () => {
      const { chat, alice, bob } = await ownerChat(cast, { shareBob: 'write' });
      await openChat(bob.page, chat);
      await scriptAnswer(ANSWER_B, 'run-b');
      await composer(bob.page).fill(Q2);
      await bob.page.getByRole('button', { name: 'Send message' }).click();
      await expect(bob.page.getByText('Team plan stays at')).toBeVisible({ timeout: 40_000 });
      await expect(bob.page.getByTestId('answered-as-label')).toHaveCount(1);
      await bob.page.getByTestId('human-message').first().scrollIntoViewIfNeeded();
      await shot(bob.page, '13', 'history-bob-tab', 'Bob\'s tab: Alice\'s turn (chip "User Owner", header note "For User Owner · their access") and Bob\'s own turn ("You", no access note).');
      await bob.page.getByTestId('human-message').last().scrollIntoViewIfNeeded();
      await openChat(alice.page, chat);
      await expect(alice.page.getByText('Team plan stays at')).toBeVisible({ timeout: 30_000 });
      await shot(alice.page, '14', 'history-alice-tab', 'Alice\'s tab of the same chat: her own turn is "You", Bob\'s turn carries "User Writer" and his answered-using label.');
    });

    test('08 composer audience notice and share-tool-results', async () => {
      const { chat, bob } = await ownerChat(cast, { shareBob: 'write', shareCarol: 'read', extraMutations: 1 });
      await openChat(bob.page, chat);
      await expect(composer(bob.page)).toBeVisible();
      await composer(bob.page).fill('Draft follow-up about pricing');
      await shot(bob.page, '15', 'composer-audience-notice', 'Bob\'s composer in a shared chat with a draft typed: the audience notice above it ("Visible to everyone in this chat · the owner can add more · files you add are shared").', composerBlock(bob.page, 'Visible to'));

      const alice = await cast.open('owner');
      const made = await alice.api.call('POST', '/api/v1/agents/agent-1/conversations/stream', { query: 'Agent: summarise the launch plan', chatMode: 'quick' });
      const agentChat = /"_id":"([0-9a-f]{24})"/.exec(typeof made.body === 'string' ? made.body : JSON.stringify(made.body))?.[1];
      if (made.status !== 200 || !agentChat) {
        unreachable('16', 'composer-share-tool-results', `agent chat could not be created through the fake (status ${made.status}: ${JSON.stringify(made.body).slice(0, 160)})`);
        return;
      }
      expect((await alice.api.call('PUT', `/api/v1/agents/agent-1/conversations/${agentChat}/collaborators`, { collaborators: [{ principalType: 'user', principalId: bob.actor.userId, accessLevel: 'write' }] })).status).toBe(200);
      await bob.page.goto(`/chat/?agentId=agent-1&conversationId=${agentChat}`);
      await expect(composer(bob.page)).toBeVisible({ timeout: 30_000 });
      await composer(bob.page).fill('Draft agent follow-up');
      await expect(bob.page.getByText('Share tool results from this message')).toBeVisible();
      await shot(bob.page, '16', 'composer-share-tool-results', 'Agent chat composer as Bob: the audience notice plus the per-message "Share tool results from this message" checkbox (off by default).', composerBlock(bob.page, 'Share tool results from this message'));
    });

    test('09 busy banner and queued send', async () => {
      const { chat, alice, bob } = await ownerChat(cast, { shareBob: 'write' });
      await openChat(alice.page, chat);
      await openChat(bob.page, chat);
      await fake.script(
        'chat_stream',
        { kind: 'held_stream', gate: 'tour-a', text: 'Checking the pricing page now.', runId: 'run-a' },
        { kind: 'stream_answer', text: ANSWER_B, runId: 'run-b' },
      );
      await composer(alice.page).fill('Is the pricing page live?');
      await alice.page.getByRole('button', { name: 'Send message' }).click();
      await waitFor('Alice run at the gate', () => fake.gateReached('tour-a'));
      const busy = bob.page.getByTestId('busy-banner');
      await expect(busy).toContainText('User Owner is asking', { timeout: 25_000 });
      await shot(alice.page, '17', 'busy-alice-streaming', 'Alice\'s own tab while her answer is held mid-stream.');
      await shot(bob.page, '18', 'busy-banner-bob', 'Bob\'s tab: busy banner "User Owner is asking…" above the composer while Alice\'s run is open.', busy);
      await composer(bob.page).fill(Q2);
      await bob.page.getByRole('button', { name: 'Send message' }).click();
      await expect(busy).toContainText('Waiting for User Owner to finish', { timeout: 10_000 });
      await shot(bob.page, '19', 'busy-queued-send', 'Bob typed and sent while Alice is busy: the message is queued and the banner reads "Waiting for User Owner to finish. Your message will be sent automatically." with Cancel.', busy);
      await fake.openGate('tour-a');
      await expect(bob.page.getByText('Team plan stays at')).toBeVisible({ timeout: 40_000 });
    });

    test('10 conversation changed notice', async () => {
      const { chat, alice, bob } = await ownerChat(cast, { shareBob: 'write' });
      let frozen = false;
      await bob.page.route('**/feed**', (route) => (frozen ? route.abort() : route.continue()));
      await openChat(bob.page, chat);
      // Bob's own first send gives his tab the stored rows (with seq); a tab loaded from the detail route alone has none.
      await scriptAnswer(ANSWER_B, 'run-b');
      await composer(bob.page).fill(Q2);
      await bob.page.getByRole('button', { name: 'Send message' }).click();
      await expect(bob.page.getByText('Team plan stays at')).toBeVisible({ timeout: 40_000 });
      frozen = true;
      let posts = 0;
      bob.page.on('request', (r) => {
        // Let the catch-up fetch through once Bob's stale send has gone out.
        if (r.method() === 'POST' && /\/messages\/stream$/.test(r.url()) && ++posts === 1) frozen = false;
      });
      await scriptAnswer('Yes, the pricing page went live this morning.', 'run-a2');
      const posted = await alice.api.call('POST', `/api/v1/conversations/${chat}/messages/stream`, { query: 'Is the pricing page live?', chatMode: 'internal_search' });
      expect(posted.status).toBe(200);
      await composer(bob.page).fill('Does annual billing include a discount?');
      await bob.page.getByRole('button', { name: 'Send message' }).click();
      const notice = bob.page.getByTestId('changed-notice');
      try {
        await expect(notice).toBeVisible({ timeout: 25_000 });
        await expect(bob.page.getByText('Is the pricing page live?').first()).toBeVisible({ timeout: 15_000 });
        await shot(bob.page, '20', 'conversation-changed-notice', 'Bob sent from a stale tab: "1 new message above. Review it, then send again." with Send again / Edit message; the draft is kept.', notice);
      } catch {
        await shot(bob.page, '20', 'conversation-changed-unreached', 'Attempt to reach the "Conversation changed" notice; the notice did not appear (see INDEX observations).');
        observe('20', 'The CONVERSATION_CHANGED notice did not appear after a stale-baseSeq send.');
      }
      observe('20', 'GET /api/v1/conversations/:id returns messages without `seq` and with `authorUserId` only, so a tab that has only loaded the detail sends no baseSeq (no stale-send protection) and, until a feed page arrives, shows author "Former member" for everyone else. Reaching CONVERSATION_CHANGED needs the tab to have seen a stored row first (here: its own earlier send).');
    });

    test('11 read-only banner', async () => {
      const { chat, carol } = await ownerChat(cast, { shareCarol: 'read' });
      await openChat(carol.page, chat);
      await expect(carol.page.getByTestId('read-only-banner')).toBeVisible();
      await expect(composer(carol.page)).toHaveCount(0);
      await shot(carol.page, '21', 'read-only-banner-carol', 'Carol (reader): "You can read this chat but not continue it." banner in place of the composer; no Share button.', carol.page.getByTestId('read-only-banner'));
    });

    test('12 access lost', async () => {
      const { chat, alice, bob } = await ownerChat(cast, { shareBob: 'write', extraMutations: 1 });
      await openChat(bob.page, chat);
      await expect(composer(bob.page)).toBeVisible();
      expect((await alice.api.revoke(chat, bob.actor)).status).toBe(200);
      const banner = bob.page.getByTestId('read-only-banner');
      await expect(banner).toContainText('You no longer have access', { timeout: 40_000 });
      await shot(bob.page, '22', 'access-lost-bob', 'Bob after Alice removed him: "You no longer have access to this chat..." banner, history still visible, no composer.', banner);
      await ensureSidebar(bob.page);
      await bob.page.getByText('New Chat', { exact: true }).click();
      await expect(composer(bob.page)).toBeVisible();
      await bob.page.evaluate((id) => window.history.pushState(null, '', `/chat/?conversationId=${id}`), chat);
      const emptyBanner = bob.page.getByTestId('read-only-banner');
      await expect(emptyBanner).toContainText("This chat isn't available to you", { timeout: 20_000 });
      await shot(bob.page, '22b', 'access-lost-empty-bob', 'Bob re-opening the chat after losing access: nothing to show, so the neutral "This chat isn\'t available to you" text (no claim about what is kept).', emptyBanner);
    });

    test('13 ask-user-question card', async () => {
      const { chat, alice, bob } = await ownerChat(cast, { shareBob: 'write' });
      await openChat(alice.page, chat);
      await openChat(bob.page, chat);
      await fake.script('chat_stream', {
        kind: 'ask_user_question',
        toolData: {
          userIntent: 'Deploy the pricing page',
          questions: [{ question: 'Which environment should I deploy to?', options: ['staging', 'production'], multiSelect: false }],
        },
      });
      await composer(bob.page).fill('Please deploy the pricing page');
      await bob.page.getByRole('button', { name: 'Send message' }).click();
      await expect(bob.page.getByText('Which environment should I deploy to?')).toBeVisible({ timeout: 40_000 });
      await bob.page.getByRole('radio', { name: 'staging' }).click();
      await shot(bob.page, '23', 'ask-card-asker-bob', 'Bob (the asker): the interactive question card with an option selected and Submit enabled.', bob.page.getByText('Which environment should I deploy to?').locator('xpath=ancestor::*[4]'));
      await expect(alice.page.getByTestId('ask-card-waiting')).toContainText('Waiting for User Writer to answer', { timeout: 30_000 });
      await shot(alice.page, '24', 'ask-card-readonly-alice', 'Alice (not the asker): the same card read-only, "Waiting for User Writer to answer", inputs disabled.', alice.page.getByTestId('ask-card-waiting'));
      await composer(alice.page).fill('User selections: staging');
      await alice.page.getByRole('button', { name: 'Send message' }).click();
      await expect(alice.page.getByText(/Only the person who was asked can answer this question/).first()).toBeVisible({ timeout: 15_000 });
      await shot(alice.page, '25', 'ask-card-resume-not-allowed-toast', 'Alice tried to answer Bob\'s question: the RESUME_NOT_ALLOWED error ("Only the person who was asked can answer this question.").');
    });

    test('13b egress confirmation card', async () => {
      const { chat, alice, bob } = await ownerChat(cast, { shareBob: 'write' });
      await openChat(alice.page, chat);
      await openChat(bob.page, chat);
      // What the agent raises after the RR #10 host guard refused a fetch to a host only Alice named.
      await fake.script('chat_stream', {
        kind: 'ask_user_question',
        toolData: {
          userIntent: 'Open the page with the launch figures',
          questions: [{
            question: 'collector.example.net came from User Owner earlier in this chat, not from you. Open https://collector.example.net/report with the launch figures?',
            options: ['Yes, open it', 'No, do not open it'],
            multiSelect: false,
          }],
        },
      });
      await composer(bob.page).fill('Pull in the launch figures');
      await bob.page.getByRole('button', { name: 'Send message' }).click();
      const question = /collector\.example\.net came from User Owner/;
      await expect(bob.page.getByText(question)).toBeVisible({ timeout: 40_000 });
      await shot(bob.page, '25b', 'egress-confirm-asker-bob', 'Bob (the sender): the confirmation card the agent raises after the egress host guard refused a fetch to a host only Alice named (RR #10). Long host and URL in the question must wrap.', bob.page.getByText(question).locator('xpath=ancestor::*[4]'));
      await expect(alice.page.getByTestId('ask-card-waiting')).toContainText('Waiting for User Writer to answer', { timeout: 30_000 });
      await shot(alice.page, '25c', 'egress-confirm-readonly-alice', 'Alice (who named the host): the same card read-only, waiting for Bob.', alice.page.getByTestId('ask-card-waiting'));
    });

    test('14 access panel', async () => {
      const launch = await fake.addTeam('Launch Team', { owner: 'READER', write_recipient: 'WRITER', team_writer: 'WRITER' });
      const { chat, alice, bob } = await ownerChat(cast, { shareBob: 'write', extraMutations: 1 });
      const teamShare = await alice.api.call('PUT', `/api/v1/conversations/${chat}/collaborators`, { collaborators: [{ principalType: 'team', principalId: launch.teamId, accessLevel: 'read' }] });
      expect(teamShare.status, JSON.stringify(teamShare.body)).toBe(200);
      // Alice leaves the team after sharing, so the explain panel has to redact it for her.
      await fake.addTeam('Launch Team', { write_recipient: 'WRITER', team_writer: 'WRITER' }, launch.teamId);
      const project = await alice.api.call('POST', '/api/v1/projects', { name: 'Q3 Launch' });
      const projectId: string = project.body.project?._id ?? project.body.project?.id;
      expect((await alice.api.call('PUT', `/api/v1/conversations/${chat}/project`, { projectId })).status).toBeLessThan(300);

      const panelOf = (page: Page) => page.getByRole('dialog', { name: 'Access to this chat' });
      await openChat(alice.page, chat);
      await alice.page.getByRole('button', { name: 'Who can access this chat' }).click();
      await expect(alice.page.getByTestId('access-explain')).toContainText('You own this chat.', { timeout: 15_000 });
      await expect(alice.page.getByRole('switch', { name: 'Visible to project members' })).toBeVisible();
      await shot(alice.page, '26', 'access-panel-owner', 'Access panel as Alice (owner): "You own this chat.", the "Check access for" picker and the Visible to project members switch (the chat is in a project).', panelOf(alice.page));
      await alice.page.getByLabel('Check access for').selectOption({ label: 'User Writer' });
      await expect(alice.page.getByTestId('access-explain')).toContainText("User Writer's access", { timeout: 15_000 });
      await shot(alice.page, '27', 'access-panel-owner-subject', 'Access panel as Alice with Bob picked as the subject: his direct path and the team path through a team Alice is not in (redacted).', panelOf(alice.page));

      await openChat(bob.page, chat);
      await bob.page.getByRole('button', { name: 'Who can access this chat' }).click();
      await expect(bob.page.getByTestId('access-explain')).toContainText('shared with you directly', { timeout: 15_000 });
      await shot(bob.page, '28', 'access-panel-direct-bob', 'Access panel as Bob: direct access (plus the Launch Team path); no subject picker.', panelOf(bob.page));

      const tw = await cast.open('team_writer');
      await openChat(tw.page, chat);
      await tw.page.getByRole('button', { name: 'Who can access this chat' }).click();
      await expect(tw.page.getByTestId('access-explain')).toContainText('through team', { timeout: 15_000 });
      await shot(tw.page, '29', 'access-panel-team-member', 'Access panel as a team-only member (User TeamWriter): "through team Launch Team".', panelOf(tw.page));
    });

    test('15 access-change dialog', async () => {
      const { chat, alice } = await ownerChat(cast, { shareBob: 'write', shareCarol: 'read', extraMutations: 1 });
      const project = await alice.api.call('POST', '/api/v1/projects', { name: 'Q3 Launch' });
      const projectId: string = project.body.project?._id ?? project.body.project?.id;
      const pv = await cast.open('project_viewer');
      const pe = await cast.open('project_editor');
      const members = await alice.api.call('PUT', `/api/v1/projects/${projectId}/members`, {
        members: [
          { principalId: pv.actor.userId, principalType: 'user', role: 'viewer' },
          { principalId: pe.actor.userId, principalType: 'user', role: 'editor' },
        ],
      });
      expect(members.status, JSON.stringify(members.body)).toBeLessThan(300);
      expect((await alice.api.call('PUT', `/api/v1/conversations/${chat}/project`, { projectId })).status).toBeLessThan(300);

      await openChat(alice.page, chat);
      const open = async () => {
        await alice.page.getByRole('button', { name: 'Who can access this chat' }).click();
        return alice.page.getByRole('switch', { name: 'Visible to project members' });
      };
      const dialog = alice.page.getByRole('dialog', { name: 'Review access changes' });
      let visibility = await open();
      await expect(visibility).toBeVisible({ timeout: 15_000 });
      await visibility.click();
      await expect(dialog.getByText('Checking who is affected…')).toHaveCount(0, { timeout: 15_000 });
      await shot(alice.page, '30', 'access-change-visibility-on', 'Review access changes: making the chat visible to project members (who gains access, who becomes read-only).', dialog);
      await dialog.getByRole('button', { name: 'Apply' }).click();
      await expect(dialog).toHaveCount(0);

      visibility = await open();
      await visibility.click();
      await expect(dialog.getByText('Checking who is affected…')).toHaveCount(0, { timeout: 15_000 });
      await shot(alice.page, '31', 'access-change-visibility-off', 'Review access changes: making the chat private again (who loses access).', dialog);
      await alice.page.keyboard.press('Escape');

      await alice.page.route('**/api/v1/authz/explain/preview', (route) => route.fulfill({ status: 500, json: { error: { code: 'INTERNAL', message: 'boom' } } }));
      visibility = await open();
      await visibility.click();
      await expect(dialog.getByText(/Couldn't check who is affected/)).toBeVisible({ timeout: 15_000 });
      await shot(alice.page, '32', 'access-change-preview-error', 'Review access changes when the preview call fails (forced 500): nothing is applied, Apply is unavailable.', dialog);
      await alice.page.keyboard.press('Escape');
      await alice.page.unroute('**/api/v1/authz/explain/preview');

    });

    test('16 sidebar badges, menu and leave dialog', async () => {
      const { chat, alice, bob } = await ownerChat(cast, { shareBob: 'write', extraMutations: 2 });
      const second = await (async () => {
        await scriptAnswer('The beta cohort has 40 accounts.', 'run-s2');
        const id = await alice.api.startChat('How big is the beta cohort?');
        await alice.api.share(id, bob.actor, 'read');
        return id;
      })();
      void second;
      // An unread turn from Alice that Bob has not seen.
      await scriptAnswer('Yes, the pricing page went live this morning.', 'run-a2');
      expect((await alice.api.call('POST', `/api/v1/conversations/${chat}/messages/stream`, { query: 'Is the pricing page live?', chatMode: 'internal_search' })).status).toBe(200);
      await bob.page.goto('/chat/');
      await ensureSidebar(bob.page);
      await bob.page.getByText('Recents', { exact: true }).click().catch(() => undefined);
      const row = bob.page.getByRole('link', { name: /When is the Q3 launch|How big is the beta/ }).first();
      await expect(row).toBeVisible({ timeout: 20_000 });
      await shot(bob.page, '35', 'sidebar-shared-badges', 'Bob\'s sidebar: shared chats with the role badge (Can continue / Can view), the shared-by subtitle and the unread dot.');
      await row.hover();
      await bob.page.getByRole('button', { name: 'Chat options' }).first().click();
      await expect(bob.page.getByRole('menuitem', { name: 'Leave' })).toBeVisible();
      await shot(bob.page, '36', 'sidebar-shared-item-menu', 'Shared-chat row menu: Archive and Leave.');
      await bob.page.getByRole('menuitem', { name: 'Leave' }).click();
      const leave = bob.page.getByRole('alertdialog').or(bob.page.getByRole('dialog', { name: /Leave this chat/ }));
      await expect(leave.first()).toBeVisible();
      await shot(bob.page, '37', 'leave-chat-dialog', 'Leave this chat? confirmation dialog.', leave);
      expect((await bob.api.call('PUT', `/api/v1/notifications/preferences/muted-sessions/${chat}`)).status).toBeLessThan(300);
      await bob.page.goto('/chat/');
      await ensureSidebar(bob.page);
      await bob.page.getByText('Recents', { exact: true }).click().catch(() => undefined);
      await expect(bob.page.getByRole('img', { name: 'Notifications muted' }).first()).toBeVisible({ timeout: 20_000 });
      await shot(bob.page, '37b', 'sidebar-muted-marker', 'Bob muted the Q3 chat: the row shows the notifications_off marker next to the role icon and unread dot.');
      await alice.page.goto('/chat/');
      await ensureSidebar(alice.page);
      await alice.page.getByText('Recents', { exact: true }).click().catch(() => undefined);
      await expect(alice.page.getByRole('img', { name: /^Shared with \d+/ }).first()).toBeVisible({ timeout: 20_000 });
      await shot(alice.page, '37c', 'sidebar-owner-shared-count', 'Alice (owner): the group icon with the number of people the chat is shared with.');
    });

    test('17 notifications panel', async () => {
      await reserveMutations(8);
      const alice = await cast.open('owner');
      const bob = await cast.open('write_recipient');
      const make = async (q: string) => {
        await scriptAnswer(`Answer to: ${q}`, 'run-n');
        return alice.api.startChat(q);
      };
      const a = await make('Notification chat: shared');
      expect((await alice.api.share(a, bob.actor, 'write', 'Please review the launch checklist and add your questions.')).status).toBe(200);
      const b = await make('Notification chat: access changed');
      await alice.api.share(b, bob.actor, 'read');
      await alice.api.share(b, bob.actor, 'write');
      const c = await make('Notification chat: ownership');
      await alice.api.share(c, bob.actor, 'write');
      expect((await alice.api.call('POST', `/api/v1/conversations/${c}/transfer-ownership`, { newOwnerUserId: bob.actor.userId })).status).toBe(200);
      const d = await make('Notification chat: deleted');
      await alice.api.share(d, bob.actor, 'write');
      // Only people who wrote in a chat hear that it was deleted.
      await scriptAnswer('Bob\'s question is answered.', 'run-bd');
      await bob.api.call('POST', `/api/v1/conversations/${d}/messages/stream`, { query: 'Quick question before this is cleaned up', chatMode: 'internal_search' });
      const e = await make('Notification chat: activity');
      await alice.api.share(e, bob.actor, 'write');
      for (const q of ['Is the pricing page live?', 'Who signs off on the launch?', 'When do we brief support?']) {
        await scriptAnswer(`Answer: ${q}`, 'run-act');
        await alice.api.call('POST', `/api/v1/conversations/${e}/messages/stream`, { query: q, chatMode: 'internal_search' });
      }
      expect((await alice.api.call('DELETE', `/api/v1/conversations/${d}`)).status).toBeLessThan(300);
      const want = ['chat.shared', 'chat.accessChanged', 'chat.ownershipTransferred', 'chat.deleted', 'chat.activity'];
      let seen = new Set<string>();
      try {
        await waitFor('five collaboration notifications', async () => {
          const list = await bob.api.call('GET', '/api/v1/notifications?limit=30');
          seen = new Set<string>((list.body.notifications ?? []).map((n: { type: string }) => n.type));
          return want.every((t) => seen.has(t));
        }, 40_000, 500);
      } catch {
        const missing = want.filter((t) => !seen.has(t));
        for (const t of missing) unreachable('38', `notification-${t}`, `the ${t} notification never reached Bob's inbox within 40 s`);
      }
      await bob.page.goto('/chat/');
      await ensureSidebar(bob.page);
      await bob.page.getByText('Inbox', { exact: true }).click();
      await expect(bob.page.getByText('Chat shared with you').first()).toBeVisible({ timeout: 20_000 });
      await shot(bob.page, '38', 'notifications-panel', 'Bob\'s inbox with the five collaboration types (shared with note, access changed, ownership transferred, deleted, coalesced activity "New activity in a shared chat: 3").');
      const activity = bob.page.getByText(/New activity in/).first().locator('xpath=ancestor::*[self::li or self::div][3]');
      await activity.hover();
      const mute = bob.page.getByRole('button', { name: 'Mute this chat' }).first();
      if (await mute.isVisible().catch(() => false)) {
        await shot(bob.page, '39', 'notifications-mute-button', 'Hovering the activity item reveals the "Mute this chat" button.');
        await mute.click();
        await expect(bob.page.getByRole('button', { name: 'Unmute this chat' }).first()).toBeAttached({ timeout: 10_000 });
        await activity.hover();
        await shot(bob.page, '40', 'notifications-muted', 'After muting, hovering the same row offers "Unmute this chat" (the row itself shows no other muted marker).');
      } else {
        unreachable('39', 'notifications-mute-button', 'no "Mute this chat" button became visible on hover');
      }
    });

    test('18 profile notification preferences', async () => {
      const bob = await cast.open('write_recipient');
      await bob.page.goto('/workspace/profile/');
      const section = bob.page.getByText('Chat notifications', { exact: true });
      await expect(section).toBeVisible({ timeout: 30_000 });
      await section.scrollIntoViewIfNeeded();
      await shot(bob.page, '41', 'profile-notification-preferences', 'Profile page, "Chat notifications" section: two email switches and the in-app activity switch.');
    });

    test('19 error toasts', async () => {
      const alice = await cast.open('owner');
      const bob = await cast.open('write_recipient');
      const carol = await cast.open('read_recipient');
      await reserveMutations(8);
      // COLLABORATOR_LIMIT for real: 199 fake teams (50 per request) fill the chat, then two people do not fit.
      const ids: string[] = [];
      for (let i = 0; i < 199; i++) ids.push((await fake.addTeam(`Team ${String(i + 1).padStart(3, '0')}`, { owner: 'READER' })).teamId);
      await scriptAnswer(ANSWER);
      const chat = await alice.api.startChat(Q1);
      for (let i = 0; i < ids.length; i += 50) {
        const r = await alice.api.call('PUT', `/api/v1/conversations/${chat}/collaborators`, {
          collaborators: ids.slice(i, i + 50).map((principalId) => ({ principalType: 'team', principalId, accessLevel: 'read' })),
        });
        if (r.status !== 200) {
          unreachable('42', 'toast-collaborator-limit', `could not fill the chat with fake teams: ${r.status} ${JSON.stringify(r.body).slice(0, 200)}`);
          return;
        }
      }
      await openChat(alice.page, chat);
      await alice.page.getByRole('button', { name: 'Share', exact: true }).first().click();
      const dialog = drawerOf(alice.page);
      const search = dialog.getByRole('textbox').first();
      await search.fill(bob.actor.email);
      await dialog.getByRole('checkbox', { name: 'User Writer' }).click();
      await search.fill(carol.actor.email);
      await dialog.getByRole('checkbox', { name: 'User Reader' }).click();
      await dialog.getByRole('button', { name: 'Share', exact: true }).click();
      await expect(alice.page.getByText(/at most 200 people and teams/).first()).toBeVisible({ timeout: 15_000 });
      await shot(alice.page, '42', 'toast-collaborator-limit', 'COLLABORATOR_LIMIT: sharing past 200 people/teams (199 already on the chat) shows "This chat can have at most 200 people and teams."', dialog);
    });

    test('15b access-change dialog on project link and unlink', async () => {
      await reserveMutations(4);
      await fake.setFlag(true, 'ENABLE_PROJECTS');
      try {
        await new Promise((r) => setTimeout(r, 11_000));
        const { chat, alice } = await ownerChat(cast, { shareBob: 'write', shareCarol: 'read' });
        const project = await alice.api.call('POST', '/api/v1/projects', { name: 'Q3 Launch' });
        const projectId: string = project.body.project?._id ?? project.body.project?.id;
        const pv = await cast.open('project_viewer');
        const pe = await cast.open('project_editor');
        await alice.api.call('PUT', `/api/v1/projects/${projectId}/members`, {
          members: [
            { principalId: pv.actor.userId, principalType: 'user', role: 'viewer' },
            { principalId: pe.actor.userId, principalType: 'user', role: 'editor' },
          ],
        });
        const row = alice.page.getByRole('link', { name: /When is the Q3 launch/ }).first();
        const reach = async () => {
          await alice.page.goto('/chat/');
          await ensureSidebar(alice.page);
          await alice.page.getByText('Recents', { exact: true }).click().catch(() => undefined);
          await expect(row).toBeVisible({ timeout: 20_000 });
        };
        const openMenu = async () => {
          await row.hover();
          await alice.page.getByRole('button', { name: /options|more/i }).first().click();
        };
        await reach();
        await openMenu();
        await shot(alice.page, '33', 'sidebar-owner-item-menu', 'Owner row menu in the sidebar with Projects on: Move to project, Rename, Archive, Delete.');
        await alice.page.getByRole('menuitem', { name: 'Move to project' }).click();
        const moveDialog = alice.page.getByRole('dialog', { name: 'Move to project' });
        await expect(moveDialog).toBeVisible();
        await moveDialog.getByText('Q3 Launch').click();
        await shot(alice.page, '34', 'move-to-project-dialog', 'Move to project dialog with the project picked.', moveDialog);
        await moveDialog.getByRole('button', { name: 'Save', exact: true }).click();
        const dialog = alice.page.getByRole('dialog', { name: 'Review access changes' });
        await expect(dialog).toBeVisible({ timeout: 15_000 });
        await expect(dialog.getByText('Checking who is affected…')).toHaveCount(0, { timeout: 15_000 });
        await shot(alice.page, '44', 'access-change-link', 'Review access changes when moving a chat into a project: who gains access / becomes read-only.', dialog);
        await dialog.getByRole('button', { name: 'Apply' }).click();
        await expect(dialog).toHaveCount(0, { timeout: 15_000 });

        // The main sidebar row has no projectId, so it cannot offer "Remove from project"; the project's own sidebar can.
        await alice.page.goto(`/chat/?projectId=${projectId}&conversationId=${chat}`);
        if ((alice.page.viewportSize()?.width ?? 1440) <= 768) await alice.page.getByRole('button', { name: /sidebar|menu/i }).first().click();
        const projectRow = alice.page.getByRole('link', { name: /When is the Q3 launch/ }).first();
        await expect(projectRow).toBeVisible({ timeout: 20_000 });
        await projectRow.hover();
        await alice.page.getByRole('button', { name: /options|more/i }).first().click();
        const remove = alice.page.getByRole('menuitem', { name: 'Remove from project' });
        if (await remove.isVisible({ timeout: 5_000 }).catch(() => false)) {
          observe('45', 'In the main sidebar the row menu of a chat that is in a project offers only "Move to project" (no "Remove from project"; "No project" cannot be saved there). Unlinking works only from the project\'s own sidebar.');
          await remove.click();
        } else {
          observe('45', 'After moving a chat into a project, no sidebar row menu offered "Remove from project" (neither the main sidebar nor the project sidebar), and Move to project > No project cannot be saved (the row has no projectId).');
          unreachable('45', 'access-change-unlink', 'no UI path to unlink a chat from its project was reachable');
          return;
        }
        await expect(dialog).toBeVisible({ timeout: 15_000 });
        await expect(dialog.getByText('Checking who is affected…')).toHaveCount(0, { timeout: 15_000 });
        await shot(alice.page, '45', 'access-change-unlink', 'Review access changes when removing a chat from its project: who loses access.', dialog);
      } finally {
        await fake.setFlag(false, 'ENABLE_PROJECTS');
      }
    });

    test('20 rate limited toast', async () => {
      await waitQuiet();
      const { chat, alice, bob } = await ownerChat(cast);
      markExhausted();
      await openChat(alice.page, chat);
      await alice.page.getByRole('button', { name: 'Share', exact: true }).first().click();
      const dialog = drawerOf(alice.page);
      await dialog.getByRole('textbox').first().fill(bob.actor.email);
      await dialog.getByRole('checkbox', { name: 'User Writer' }).click();
      // The limiter counts in fixed one-minute windows, so a burst can straddle a boundary and leave budget in the new
      // window. Burn it right before the click and stop at the first 429.
      for (let i = 0; i < 45; i++) {
        const r = await alice.api.call('DELETE', `/api/v1/conversations/${chat}/collaborators/${bob.actor.userId}?principalType=user`);
        if (r.status === 429) break;
      }
      await dialog.getByRole('button', { name: 'Share', exact: true }).click();
      await expect(alice.page.getByText(/Too many changes/).first()).toBeVisible({ timeout: 15_000 });
      await shot(alice.page, '43', 'toast-rate-limited', 'RATE_LIMITED: the 21st sharing change inside a minute shows "Too many changes. Try again in N seconds." (real limiter, forced with 22 delete calls).', dialog);
      markExhausted();
    });
  });
}

test('zz write INDEX.md', () => {
  writeIndex();
  observe('end', 'index written');
  void unreachable;
  void waitFor;
});
