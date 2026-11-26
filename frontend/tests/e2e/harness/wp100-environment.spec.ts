/**
 * WP-100's Environment section in the agent builder on a real stack (case list:
 * docs/agent-harness-v2/ui-cases/wp100-environments.json; R19-FU-3). The agent saves through the builder's own Save
 * draft, on the stack's agent routes; no model is called and nothing runs.
 *
 *   UI-ENV-1  Setup steps: commands and settings save as the agent's environment and show again after a reload
 *   UI-ENV-2  From devcontainer.json: a pasted file names what is not used (its image)
 *   UI-ENV-3  A setting that looks like a secret is named, with "connect it as an account instead"
 *
 * Shots with UI_CASE_SHOTS: `ui-env-1-steps`, `ui-env-2-devcontainer`, `ui-env-3-secret` (support/case-shots.ts).
 */
import { randomBytes } from 'crypto';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { followSystemTheme, iconFonts, shoot } from './support/case-shots';
import { expectCardsFitAtEveryWidth, flowCards } from './support/card-fit';
import { expectCardsReadableInBothThemes } from './support/card-contrast';

const commands = (page: Page) => page.getByLabel('Setup commands');
const settings = (page: Page) => page.getByLabel('Settings (one per line, NAME=value)');
const how = (page: Page) => page.getByRole('radiogroup', { name: 'How the environment is set up' });
// The buttons' accessible names carry their icon's ligature first ("save Save draft"), so names match at the end.
const saveDraft = (page: Page) => page.getByRole('button', { name: /Save draft$/ });

test.describe('WP-100 Environment, agent builder', () => {
  let agentKey: string | null = null;

  test.beforeEach(async ({ page, apiContext }) => {
    await followSystemTheme(page);
    await iconFonts(page);
    const created = await apiContext.post('/api/v1/agents/create', {
      data: {
        name: `Environment ${randomBytes(3).toString('hex')}`,
        description: '',
        startMessage: '',
        systemPrompt: '',
        models: [],
        tags: [],
        shareWithOrg: false,
        isServiceAccount: false,
      },
    });
    expect(created.ok(), await created.text()).toBe(true);
    agentKey = ((await created.json()) as { agent: { _key: string } }).agent._key;
  });

  test.afterEach(async ({ apiContext }) => {
    if (agentKey) await apiContext.delete(`/api/v1/agents/${agentKey}`);
    agentKey = null;
  });

  test('UI-ENV-1: setup steps and settings save as the agent’s environment and read back', async ({ page, apiContext }) => {
    await page.goto(`/agents/edit/?agentKey=${agentKey!}`);
    await expect(commands(page)).toBeAttached({ timeout: 60_000 });
    await commands(page).fill('pip install -r requirements.txt');
    await settings(page).fill('LANG=C.UTF-8');
    await expect(saveDraft(page)).toBeEnabled();
    const saved = page.waitForResponse((r) => r.request().method() === 'PUT' && r.url().includes(`/api/v1/agents/${agentKey!}`));
    await saveDraft(page).click();
    const response = await saved;
    expect(response.ok(), await response.text()).toBe(true);
    expect(JSON.stringify(response.request().postDataJSON())).toContain('pip install -r requirements.txt');
    const stored = JSON.stringify(await (await apiContext.get(`/api/v1/agents/${agentKey!}`)).json());
    expect(stored).toContain('pip install -r requirements.txt');
    expect(stored).toContain('C.UTF-8');

    await page.reload();
    await expect(commands(page)).toHaveValue('pip install -r requirements.txt', { timeout: 60_000 });
    await expect(settings(page)).toHaveValue('LANG=C.UTF-8');
    await expectCardsFitAtEveryWidth(page, () => flowCards(page), 'UI-ENV-1 agent builder cards');
    await expectCardsReadableInBothThemes(page, () => flowCards(page), 'UI-ENV-1 agent builder cards');
    await shoot(page, 'ui-env-1-steps', () => commands(page), { pans: true });
  });

  test('UI-ENV-2: a pasted devcontainer.json names what is not used', async ({ page }) => {
    await page.goto(`/agents/edit/?agentKey=${agentKey!}`);
    await expect(how(page)).toBeAttached({ timeout: 60_000 });
    await how(page).getByRole('radio', { name: 'From devcontainer.json' }).click();
    await page
      .getByLabel('devcontainer.json')
      .fill('{"image": "python:3.12", "containerEnv": {"LANG": "C.UTF-8"}, "postCreateCommand": "make setup"}');
    const note = page.getByText(/^Not used: image/);
    await expect(note).toBeVisible();
    await expectCardsFitAtEveryWidth(page, () => flowCards(page), 'UI-ENV-2 agent builder cards');
    await expectCardsReadableInBothThemes(page, () => flowCards(page), 'UI-ENV-2 agent builder cards');
    await shoot(page, 'ui-env-2-devcontainer', () => note, { pans: true });
  });

  test('UI-ENV-3: a setting that looks like a secret is named and not saved', async ({ page }) => {
    await page.goto(`/agents/edit/?agentKey=${agentKey!}`);
    await expect(settings(page)).toBeAttached({ timeout: 60_000 });
    await settings(page).fill('GITHUB_TOKEN=not-a-real-token');
    const problem = page.getByRole('alert').filter({ hasText: 'GITHUB_TOKEN looks like a secret' });
    await expect(problem).toContainText('Connect it as an account instead');
    await expectCardsFitAtEveryWidth(page, () => flowCards(page), 'UI-ENV-3 agent builder cards');
    await expectCardsReadableInBothThemes(page, () => flowCards(page), 'UI-ENV-3 agent builder cards');
    await shoot(page, 'ui-env-3-secret', () => problem, { pans: true });
  });
});
