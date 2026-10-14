/**
 * Building an agent and talking to it, end to end: create it, give it
 * instructions in the builder, save, open it in chat and get a reply that
 * follows those instructions.
 *
 * Nothing is mocked. The instructions hold a passphrase that exists nowhere
 * else, so a reply containing it proves the saved instructions reached the
 * model through this agent, not the plain chat.
 */
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import { hasAnsweringModels, NO_MODEL_REASON } from '../helpers/ai-models.helper';

test.describe('Build an agent and chat with it', () => {
  let agentKey: string | undefined;

  test.beforeEach(async ({ apiContext }) => {
    // The ai-models setup step adds them when credentials are available.
    test.skip(!(await hasAnsweringModels(apiContext)), NO_MODEL_REASON);
  });

  test.afterEach(async ({ apiContext }) => {
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
    await create.getByRole('button', { name: 'Create agent' }).click();
    await expect(page).toHaveURL(/\/agents\/edit\/\?agentKey=/, { timeout: 30_000 });
    agentKey = new URL(page.url()).searchParams.get('agentKey') ?? undefined;
    expect(agentKey, 'the builder URL should carry the new agent key').toBeTruthy();

    await page.getByRole('button', { name: 'Edit prompts' }).click();
    const prompts = page.getByRole('dialog', { name: 'Configure Agent Prompts' });
    // The first box is the system prompt; the labels are not tied to the inputs.
    await prompts
      .getByRole('textbox')
      .first()
      .fill(`You are the Kestrel launch assistant. Whenever someone greets you, reply with the passphrase ${passphrase}.`);
    await prompts.getByRole('button', { name: 'Save' }).click();

    const saved = page.waitForResponse(
      (r) => r.request().method() === 'PUT' && r.url().includes(`/api/v1/agents/${agentKey}`),
    );
    await page.getByRole('button', { name: 'Save changes' }).click();
    expect((await saved).ok(), 'saving the agent should succeed').toBe(true);

    const confirmation = page.getByRole('dialog').filter({ hasText: 'Agent updated' });
    await expect(confirmation).toBeVisible({ timeout: 15_000 });
    await confirmation.getByRole('button', { name: 'Open in chat' }).click();
    await expect(page).toHaveURL(new RegExp(`/chat/\\?agentId=${agentKey}`), { timeout: 30_000 });
    await expect(page.getByRole('button', { name: 'Agent options' })).toContainText(name, { timeout: 30_000 });

    const input = page.locator('textarea').last();
    await input.fill('Hello!');
    await input.press('Enter');
    await expect(page.getByText(passphrase).first(), 'the reply should follow the agent\'s instructions').toBeVisible({
      timeout: 180_000,
    });
  });
});
