/**
 * Building an agent and talking to it, end to end: create it, give it
 * instructions in the builder, publish, open it in chat and get a reply that
 * follows those instructions.
 *
 * Nothing is mocked. The instructions hold a passphrase that exists nowhere
 * else, so it reaching the model proves the published instructions went
 * through this agent, not the plain chat. With a real model (project `ai`) the
 * reply must say it. On the harness lane (project `harness`) the scripted model
 * cannot follow instructions, so the turn scripts its reply and the model
 * reports the passphrase reached it in a request the person never typed it in.
 */
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import { hasAnsweringModels, NO_MODEL_REASON } from '../helpers/ai-models.helper';
import { cancelLeftRuns, lane, script, ScriptedModel } from '../harness/support/lane';

const scripted = () => test.info().project.name === 'harness';

test.describe('Build an agent and chat with it', () => {
  let agentKey: string | undefined;
  let model: ScriptedModel | undefined;

  test.beforeEach(async ({ apiContext }) => {
    if (scripted()) {
      model = await ScriptedModel.open(lane());
      await model.reset();
      return;
    }
    // The ai-models setup step adds them when credentials are available.
    test.skip(!(await hasAnsweringModels(apiContext)), NO_MODEL_REASON);
  });

  test.afterEach(async ({ apiContext }) => {
    if (model) {
      await model.watchFor([]);
      await cancelLeftRuns(apiContext);
      await model.close();
      model = undefined;
    }
    if (!agentKey) return;
    const res = await apiContext.delete(`/api/v1/agents/${agentKey}`);
    agentKey = undefined;
    expect(res.ok() || res.status() === 404, `deleting the test agent failed: ${res.status()}`).toBe(true);
  });

  test('an agent created in the builder answers in chat as instructed', async ({ page }) => {
    test.setTimeout(240_000);
    const name = `E2E Agent ${Date.now().toString(36)}`;
    const passphrase = `E2EFACT-${randomBytes(4).toString('hex').toUpperCase()}`;

    await page.goto('/agents/new/');
    const create = page.getByRole('dialog', { name: 'Create agent' });
    await create.getByPlaceholder('e.g. Support bot').fill(name);
    await create.getByRole('button', { name: /Create agent$/ }).click();
    await expect(page).toHaveURL(/\/agents\/edit\/\?agentKey=/, { timeout: 30_000 });
    agentKey = new URL(page.url()).searchParams.get('agentKey') ?? undefined;
    expect(agentKey, 'the builder URL should carry the new agent key').toBeTruthy();

    await page.getByRole('button', { name: 'Edit prompts' }).click();
    const prompts = page.getByRole('dialog', { name: 'Configure agent prompts' });
    // The first box is the system prompt; the labels are not tied to the inputs.
    await prompts
      .getByRole('textbox')
      .first()
      .fill(`You are the Kestrel launch assistant. Whenever someone greets you, reply with the passphrase ${passphrase}.`);
    await prompts.getByRole('button', { name: 'Save' }).click();

    // Agent versions (WP-21): the builder saves a draft, and Publish makes it what chats run. The buttons' names
    // carry their icon's ligature first.
    await page.getByRole('button', { name: /Publish$/ }).click();
    const confirm = page.getByRole('alertdialog');
    await expect(confirm.getByRole('heading', { name: /^Publish version \d+\?$/ })).toBeVisible();
    await confirm.getByRole('button', { name: 'Publish', exact: true }).click();
    const published = page.getByRole('dialog', { name: /^Version \d+ published$/ });
    await expect(published, 'publishing the agent should succeed').toBeVisible({ timeout: 30_000 });
    await published.getByRole('button', { name: /Open in chat$/ }).click();
    await expect(page).toHaveURL(new RegExp(`/chat/\\?agentId=${agentKey}`), { timeout: 30_000 });
    await expect(page.getByRole('button', { name: 'Agent options' })).toContainText(name, { timeout: 30_000 });

    const input = page.locator('textarea').last();
    if (model) {
      await model.watchFor([passphrase]);
      const reply = `Kestrel ${randomBytes(3).toString('hex')} is ready.`;
      await input.fill(script('Hello!', `say ${reply}`));
      await input.press('Enter');
      await expect(page.getByText(reply, { exact: true }).first(), 'the scripted reply lands in the agent chat').toBeVisible({
        timeout: 90_000,
      });
      const seen = await model.sightings();
      expect(seen.length, 'the published instructions reached the model with the turn').toBeGreaterThan(0);
      return;
    }
    await input.fill('Hello!');
    await input.press('Enter');
    await expect(page.getByText(passphrase).first(), 'the reply should follow the agent\'s instructions').toBeVisible({
      timeout: 180_000,
    });
  });
});
